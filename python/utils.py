import array
import glob
import json
import os
import __future__
from subprocess import check_output

import cv2
import h5py
import hickle as hkl
import numpy as np
import natsort
import pandas as pd


def c3d_input_file_generator(filename, output_file, t_size=16, step_size=8,
                             output_folder=None, mkdir=True):
    """Generate textfile(s) used by C3D code

    Parameters
    ----------
    filename : string
        fullpath of CSV file (space separated of 4-5 columns with header) with
        data about the videos to process. Expected columns are:
        ['video-name', 'num-frame', 'i-frame', 'duration', 'label']
    output_file : string or list
        list of output files to generate
    t_size : int, optional
        size of temporal field of your C3D network.
    step_size : int, optional
        control how dense do you plan to extract clips of your long video.
    output_folder : string, optional
        fullpath of folder to allocate outputs. Pass it to generate output-file
        for extract-image-features.
    mkdir : bool, optional
        create folder to allocate outputs for C3D extract-image-features

    Outputs
    -------
    summary : dict
        statistics about the process

    """
    summary = dict(success=False, output=None)
    req_cols = set(['video-name', 'num-frame', 'i-frame', 'duration'])
    try:
        df = pd.read_csv(filename, sep=' ')
    except:
        raise ValueError('Unable to open file {}'.format(filename))

    cols = df.columns.tolist()
    n_segments = df.shape[0]
    if not req_cols.issubset(cols):
        msg = 'Not enough information or incorrect format in {}'
        raise ValueError(msg.format(filename))

    if 'label' not in cols:
        dummy = np.zeros(n_segments, int)
        df = pd.concat([df, pd.DataFrame({'label': dummy})], axis=1)

    if isinstance(output_file, str):
        output_file = [output_file]

    idx_bool_keep = df['duration'] >= t_size
    # Compute number of clips and from where to extract clips from each
    # activity-segment
    n_clips = ((df['duration'] - t_size) / step_size + 1).astype('int')
    init_end_frame = np.concatenate(
        [np.arange(df.loc[i, 'i-frame'],
                   df.loc[i, 'i-frame'] + df.loc[i, 'duration'],
                   step_size)[:n_clips[i]]
         for i in df.index[idx_bool_keep]])

    # Create new data frame with data required for C3D input layer
    idx_keep_expanded = np.repeat(np.array(df.index[idx_bool_keep]),
                                  n_clips[idx_bool_keep])
    df_c3d = pd.DataFrame({1: df.loc[idx_keep_expanded, 'video-name'],
                           2: init_end_frame,
                           3: df.loc[idx_keep_expanded, 'label']})
    df_c3d.drop_duplicates(keep='first', inplace=True)

    # Dump DataFrame for C3D-input
    try:
        df_c3d.to_csv(output_file[0], sep=' ', header=False, index=False)
    except:
        msg = 'Unable to create input list for C3D ({}). Check permissions.'
        raise ValueError(msg.format(output_file[0]))

    # C3D-output
    if len(output_file) > 1 and isinstance(output_folder, str):
        sr_out = (output_folder + os.path.sep +
                  df.loc[idx_keep_expanded, 'video-name'].astype(str) +
                  os.path.sep + init_end_frame.astype(str))
        # Avoid redundacy such that A//B introduce by previous cmd. A
        # principled solution is welcome.
        sr_out = sr_out.apply(os.path.normpath)
        sr_out.drop_duplicates(keep='first', inplace=True)
        skip_mkdir = False

        # Dump DataFrame for C3D-output
        try:
            sr_out.to_csv(output_file[1], header=None, index=False)
        except:
            skip_mkdir = True

        # Create dirs to place C3D-features
        if not skip_mkdir:
            summary['output'] = output_file[1]
            if mkdir:
                for i in df['video-name'].unique():
                    os.makedirs(os.path.join(output_folder, i))

    # Update summary
    summary['success'] = True
    summary['pctg-skipped-segments'] = ((n_segments - idx_bool_keep.sum()) *
                                        1.0 / n_segments)
    summary['ratio-clips-segments'] = (idx_keep_expanded.size * 1.0 /
                                       n_segments)
    return summary


def c3d_read_feature(filename):
    """Read feature dump by C3D

    Parameters
    ----------
    filename : str
        Fullpath of file to read

    Outputs
    -------
    x : ndarray
        numpy array of features

    Note: It accomplishes the same purpose of this code:
        C3D/examples/c3d_feature_extraction/script/read_binary_blob.m

    """
    s_parr, d_parr = array.array('i'), array.array('f')
    with open(filename, 'r') as f:
        s_parr.fromfile(f, 5)
        s = np.array(s_parr)
        m = np.cumprod(s)[-1]

        d_parr.fromfile(f, m)
    return s, np.array(d_parr)


def c3d_stack_feature(dirname, files=None, layer='.fc7-1', savefile=None,
                      pool_type=None):
    """Read C3D features from disk and stack them as ndarray

    Parameters
    ----------
    dirname : str.
        Fullpath of dirname with C3D-output.
    files : list, optional.
        List of basename files without extension to read. By default, stack all
        the features associated with the layer of interest.
    layer : str, optional.
        Layer of interest to read.
    save : str, optional.
        Dump features in one single file.
    pool_type : str, optional.
        Global pooling strategy over a bunch of features. Choices are limited
        'mean', 'max', 'pyr:l,mean/max'.

    Outputs
    -------
    arr : ndarray
        2-dim array of shape [m x d]. d:= is the dimensionality of the feature
        space. m := number of features stacked. If pooling is applied, m = 1.

    Note: It just stacks several flatten 1-dim array read by c3d_read_feature.

    """
    if not os.path.exists(dirname):
        raise IOError('Unexistent folder {}'.format(dirname))

    # Get files to read
    if files is not None and isinstance(files, list):
        c3d_files = [None] * len(files)
        for i, v in enumerate(files):
            c3d_files[i] = os.path.join(dirname, str(v) + layer)
    else:
        c3d_files = glob.glob(os.path.join(dirname, '*' + layer))

    sorted_files = natsort.natsorted(c3d_files)
    # Initialize ndarray
    s, data = c3d_read_feature(sorted_files[0])
    arr = np.empty((len(sorted_files), np.cumprod(s)[-1]))
    arr[0, ...] = data

    # Read most of the features
    for i, v in enumerate(sorted_files[1::]):
        _, data = c3d_read_feature(v)
        arr[i, ...] = data

    # Apply pooling
    if isinstance(pool_type, str):
        pool_type = pool_type.lower()
        if pool_type == 'mean':
            arr = arr.mean(axis=0)
        elif pool_type == 'max':
            arr = arr.max(axis=0)
        elif 'pyr' in pool_type:
            level, pool_type = pool_type.split(':')[1].split(',')
            arr = feature_1dpyramid(arr, int(level), pool_type)
        else:
            raise ValueError('Unknown pool_type: ' + pool_type)
        arr = np.expand_dims(arr, axis=0)

    # Save if required
    if isinstance(savefile, str):
        if len(os.path.splitext(savefile)[1]) <= 0:
            savefile += '.hkl'
        hkl.dump(arr, savefile, mode='w', compression='gzip',
                 compression_opts=9)
    return arr


def c3d_batch_feature_stacking(df, dirname, t_size=16, t_stride=8,
                               feat_size=4096, savedir=None, stack_prm=None,
                               persistent=None, h5mode='x', h5prm=None):
    """Stack C3D feature for a bunch of segments

    Parameters
    ----------
    df : DataFrame.
        Table data with (at  least) the following column names:
        ['video-name', 'f-init', 'duration'].
    dirname : str.
        Fullpath of root folder with features.
    t_size : int, optional.
        Size of temporal receptive field C3D-model.
    t_stride : int, optional.
        Size of temporal stride btw features.
    savedir : str, optional.
        Fullpath of directory to save results, if required.
    stack_prm : dict.
        Parameters for c3d_stack_feature function.
    persistent : str, optional
        save stack of feature-arrays as HDF5. Use it when your memory is scarce
        wrt to the number of rows on df and feature size.
    h5mode : str, optional.
        Mode to open hdf5 file.
    h5prm : dict, optional.
        dict with parameter for HDF5 saved on persistent mode.

    Outputs
    -------
    arr : ndarray.
        3-dim array of shape [df.shape[0], m, d] where d := dimensionality of
        the feature space and m is df.loc[i, 'duration']

    Notes
    -----
    It assumes that segments in df have the same length

    """
    n, T = df.shape[0], df.loc[:, 'duration'].min()
    Te = (T - t_size) / t_stride + 1
    if stack_prm is None:
        stack_prm = {}
    if h5prm is None:
        h5prm = dict(chunks=True, compression='lzf')

    def wrapper_c3d_stacking(video_name, f_init, duration, dirname=dirname,
                             T=t_size, s=t_stride, savedir=savedir,
                             stack_prm=stack_prm):
        # Extra 1 comes from zero-indexing
        frames_of_interest = range(f_init, f_init + duration - T + 1, s)
        dirname_video = os.path.join(dirname, video_name)
        args = (dirname_video, frames_of_interest)

        if isinstance(savedir, str):
            outfile = os.path.join(savedir, video_name + '_' + str(f_init))
            tmp = c3d_stack_feature(*args, savefile=outfile, **stack_prm)
        else:
            tmp = c3d_stack_feature(*args, **stack_prm)

        return tmp

    if persistent:
        f = h5py.File(persistent, h5mode)
        arr = f.create_dataset('segment_features', (n, Te, feat_size),
                               dtype='float32', **h5prm)
        arr.dims[0].label = 'batch'
        arr.dims[1].label = 't_step'
        arr.dims[2].label = 'feature'
    else:
        arr = np.empty((n, T, feat_size), dtype=np.float32)

    for i, v in enumerate(df.index):
        arr[i, ...] = wrapper_c3d_stacking(*df.loc[v, ['video-name', 'f-init',
                                                       'duration']])

    if persistent:
        f.close()
    else:
        return arr


# General utilities
def balance_labels(Y, batch_size=1):
    """Compute weights to balance distribution btw 1/0

    Parameters
    ----------
    Y : ndarray
        Binary Label matrix or vector {0, 1}.
    batch_size : int, optional
        mini-batch size

    Outputs
    -------
    w_pos : float
        Weight for samples labeled as 1
    w_neg : float
        Weight for samples labeled as 0

    """
    w_pos = Y.sum() * 1.0 / batch_size
    w_neg = Y.size * 1.0 / batch_size - w_pos

    if w_pos > w_neg:
        w_neg, w_pos = 1.0, w_neg / w_pos
    elif w_pos < w_neg:
        w_neg, w_pos = w_pos / w_neg, 1.0
    else:
        w_pos = w_neg = 1.0
    return w_pos, w_neg


def feature_1dpyramid(x, levels=0, pool_type='mean', norm=True, unit=False):
    """Compute a 1d pyramid representation of a feature vector

    Parameters
    ----------
    x : ndarray
        [m x d] array of features. m is the number of features and d is the
        dimensionality of the feature space.
    levels : int
        Number of levels of the pyramid representation.
    pool_type : str
        Pooling strategy over a bunch of features.
    norm : bool
        Normalize each region before concatenate them.
    unit : bool
        Normalize the final input vector.

    Outputs
    -------
    [d * (2**(levels + 1) - 1)] ndarray with pyramid represetantion of x.

    """
    m, d = x.shape
    arr = [np.empty(d) for i in range((2**(levels + 1) - 1))]
    pool_type = pool_type.lower()

    idx = 0
    for i in range(levels + 1):
        n = 2 ** i
        edges = np.ones(n + 1, dtype=int) * 1.0 / n
        edges[0] = 0
        edges = np.round(np.cumsum(edges) * m).astype(int)
        for j in range(n):
            if pool_type == 'mean':
                arr[idx][...] = x[edges[j]:edges[j + 1], :].mean(axis=0)
            elif pool_type == 'max':
                arr[idx][...] = x[edges[j]:edges[j + 1], :].max(axis=0)
            else:
                raise ValueError('Unknown pooling type {}'.format(pool_type))

            if norm:
                feat_norm = np.sqrt((arr[idx] ** 2).sum())
                if feat_norm == 0:
                    feat_norm = 1.0
                arr[idx] /= feat_norm

            idx += 1
    pyr_feat = np.hstack(arr)
    if unit:
        return pyr_feat / (2**(levels + 1) - 1)
    return pyr_feat


def feature_1dconcat(x, n=8, pool_type='mean', norm=True, unit=False):
    """Compute a 1d pyramid representation of a feature vector
    Parameters
    ----------
    x : ndarray.
        [m x d] array of features. m is the number of features and d is the
        dimensionality of the feature space.
    n : int
        Number of chunks.
    pool_type : str.
        Pooling strategy over a bunch of features.
    norm : bool.
        Normalize each region before concatenate them.
    unit : bool.
        Normalize the final input vector.
    Outputs
    -------
    [d * n] ndarray with concat feature of x.
    """
    m, d = x.shape
    arr = [np.empty(d) for i in range(n)]
    pool_type = pool_type.lower()

    edges = np.ones(n + 1, dtype=int) * 1.0 / n
    edges[0] = 0
    edges = np.round(np.cumsum(edges) * m).astype(int)
    for j in range(n):
        if pool_type == 'mean':
            arr[j][...] = x[edges[j]:edges[j + 1], :].mean(axis=0)
        elif pool_type == 'max':
            arr[j][...] = x[edges[j]:edges[j + 1], :].max(axis=0)
        else:
            raise ValueError('Unknown pooling type {}'.format(pool_type))

        if norm:
            feat_norm = np.sqrt((arr[j] ** 2).sum())
            if feat_norm == 0:
                feat_norm = 1.0
            arr[j] /= feat_norm

    concat_feat = np.hstack(arr)
    if unit:
        return concat_feat / n
    return concat_feat


def idx_of_queries(df, col_name, queries, n_samples=None, rng_seed=None):
    """Return indexes of several queries on a DataFrame

    Parameters
    ----------
    df : DataFrame
    col_name : int, str
    queries : list, series, 1-dim ndarray
    n_samples : int
    rng_seed : rng instance or int

    Outputs
    -------
    idx : ndarray
        1-dim array of index over of queries

    """
    idx_lst = [None] * queries.size
    # There should be a pandas way of doing this
    for i, v in enumerate(queries):
        idx_lst[i] = (df[col_name] == v).nonzero()[0]
    idx = np.hstack(idx_lst)

    if n_samples is None:
        return idx
    elif isinstance(n_samples, int):
        if rng_seed is None or isinstance(rng_seed, int):
            rng = np.random.RandomState(rng_seed)
        else:
            rng = rng_seed

        return rng.permutation(idx)[:n_samples]


# Video utilities
def count_frames(filename, method=None, ext='*.jpg'):
    """Count number of frames of a video

    Parameters
    ----------
    filename : string
        fullpath of video file
    method : string, optional
        algorithm to use (None, 'ffprobe')
    ext : string, optional
        image extension

    Outputs
    -------
    counter : int
        number of frames

    """
    counter, fail_ffprobe = 0, False
    if isinstance(method, str):
        if method == 'ffprobe':
            cmd = ['ffprobe', '-v', 'error', '-count_frames',
                   '-select_streams', 'v:0', '-show_entries',
                   'stream=nb_read_frames', '-of',
                   'default=nokey=1:noprint_wrappers=1', filename]
            try:
                counter = int(check_output(cmd).replace('\n', ''))
            except:
                counter, fail_ffprobe = 0, True
        else:
            if os.path.isdir(filename):
                counter = len(glob.glob(os.path.join(filename, ext)))

    if method is None or fail_ffprobe:
        cap = cv2.VideoCapture(filename)
        while cap.isOpened():
            ret = cap.grab()
            if ret:
                counter += 1
            else:
                break
        cap.release()
    return counter


def dump_frames(filename, output_folder, basename_format=None):
    """Dump frames of a video-file into a folder

    Parameters
    ----------
    filename : string
        Fullpath of video-file
    output_folder : string
        Fullpath of folder to place frames
    basename_format: (None, string)
        String format used to save video frames. If None, the
        format is assigned according the length of the video

    Outputs
    -------
    success : bool

    Note: this function makes use of ffmpeg and its results depends on it.

    """
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)
    if basename_format:
        fbasename = basename_format + '.jpg'
    else:
        n_frames, n = count_frames(filename, 'ffprobe'), 0
        while n_frames > 0:
            n_frames /= 10
            n += 1
        fbasename = '%0' + str(max(6, n)) + 'd.jpg'

    output_format = os.path.join(output_folder, fbasename)
    cmd = ['ffmpeg', '-v', 'error', '-i', filename, '-qscale:v', '2', '-f',
           'image2', output_format]
    try:
        check_output(cmd)
    except:
        return False
    return True


def dump_video(filename, clip, fourcc_str='X264', fps=30.0):
    """Write video on disk from a stack of images

    Parameters
    ----------
    filename : str
        Fullpath of video-file to generate
    clip : ndarray
        ndarray where first dimension is used to refer to i-th frame
    fourcc_str : str
        str to retrieve fourcc from opencv
    fps : float
        frame rate of create video-stream

    """
    fourcc = cv2.cv.CV_FOURCC(**list(fourcc_str))
    fid = cv2.VideoWriter(filename, fourcc, fps, clip.shape[0:2])
    if fid.isOpened():
        for i in xrange(clip.shape[0]):
                fid.write(clip[i, ...])
        return True
    else:
        return False


def frame_rate(filename):
    """Return frame-rate of video

    Parameters
    ----------
    filename : stri
        Fullpath of video-file

    Outputs
    -------
    frame_rate : float

    Note: this function makes use of ffprobe and its results depends on it.

    """
    if os.path.isfile(filename):
        cmd = ('ffprobe -v 0 -of flat=s=_ -select_streams v:0 -show_entries ' +
               'stream=avg_frame_rate -of default=nokey=1:noprint_wrappers=1' +
               ' ' + filename).split()
        fr_exp = check_output(cmd)
        return eval(compile(fr_exp, '<string>', 'eval',
                            __future__.division.compiler_flag))
    else:
        return 0.0


def get_clip(filename, i_frame=0, duration=1, ext='.jpg', img_list=None):
    """Return a clip from a video

    Parameters
    ----------
    filename : str
        Fullpath of video-stream or img-dir
    i_frame : int, optional
        Index of initial frame to capture, 0-indexed.
    duration : int, optional
        duration of clip
    ext : str
        Extension of image-files in case filename is dir
    img_list : list, optional
        list, is a set of strings with basename of images to stack.

    Outputs
    -------
    clip : ndarray
        numpy array of stacked frames

    """
    clip = []
    if os.path.isdir(filename):
        if img_list is None:
            img_files = glob.glob(os.path.join(filename, '*' + ext))
            img_files_s = natsort.natsorted(img_files)
            img_list = img_files_s[i_frame:i_frame + duration]

        # Make a clip from a list of images in filename dir
        if isinstance(img_list, list):
            for i in img_list:
                img_name = i
                if filename not in i:
                    img_name = os.path.join(filename, i)

                if os.path.isfile(img_name):
                    img = cv2.imread(img_name)
                    if img is not None:
                        clip.append(img)
                else:
                    raise IOError('unknown file {}'.format(img_name))
    elif os.path.isfile(filename):
        cap = cv2.VideoCapture(filename)
        for i in xrange(0, i_frame):
            success = cap.grab()
        for i in xrange(0, duration):
            success, img = cap.read()
            if success:
                clip.append(img)
            else:
                break
        cap.release()
    else:
        return None
    return np.stack(clip)


def video_duration(filename):
    """Return frame-rate of video

    Parameters
    ----------
    filename : stri
        Fullpath of video-file

    Outputs
    -------
    frame_rate : float

    Note: this function makes use of ffprobe and its results depends on it.

    """
    if os.path.isfile(filename):
        cmd = ('ffprobe -v 0 -of flat=s=_ -select_streams v:0 -show_entries ' +
               'stream=duration -of default=nokey=1:noprint_wrappers=1 ' +
               filename).split()
        fr_exp = check_output(cmd)
        return eval(compile(fr_exp, '<string>', 'eval',
                            __future__.division.compiler_flag))
    else:
        return 0.0


# Sampling
def sampling_with_uniform_groups(x, bin_edges, strict=True, rng=None):
    """
    Sample values of x such that the distribution on bin-edges is as uniform as
    possible

    Parameters
    ----------
    x : ndarray
        1-dim array to sample
    bin_edges : ndarray
        1-dim array with intervals. See numpy.digitize for more details.
    strict : bool, optional
        If true, every bucket will have the same number of samples.
    rng : numpy.random.RandomState, optional
        pseudo-rnd number generator instance

    Outputs
    -------
    keep_idx : ndarray
        1-dim array of indexes from the elements of x to keep

    """
    if rng is None:
        rng = np.random.RandomState()

    n_bins = len(bin_edges) - 1
    idx = np.digitize(x, bin_edges) - 1
    counts = np.bincount(idx, minlength=n_bins)

    min_samples = counts.min()
    if strict:
        samples_per_bin = np.repeat(min_samples, n_bins)
        # Sample from the same distrib withot matter strict value
        rng.rand(n_bins)
    else:
        samples_per_bin = np.minimum(min_samples+counts.std()*rng.rand(n_bins),
                                     counts)

    keep_idx_list = []
    for i in xrange(n_bins):
        tmp = (idx == i).nonzero()[0]
        keep_idx_list.append(rng.permutation(tmp)[:samples_per_bin[i]])
    keep_idx = np.hstack(keep_idx_list)
    return keep_idx


# Segment utilities
def segment_format(X, mthd='c2b', T=None, init=None):
    """Transform temporal annotations

    Parameters
    ----------
    X : ndarray
        [n x 2] array with temporal annotations
    mthd : str
        Type of conversion:
        'c2b': transform [center, duration] onto [f-init, f-end]
        'b2c': inverse of c2b
        'd2b': transform ['f-init', 'n-frames'] into ['f-init', 'f-end']

    Outputs
    -------
    Y : ndarray
        [n x 2] array with transformed temporal annotations

    """
    if X.ndim != 2:
        msg = 'Incorrect number of dimensions. X.shape = {}'
        ValueError(msg.format(X.shape))

    if mthd == 'c2b':
        Xinit = np.ceil(X[:, 0] - 0.5*X[:, 1])
        Xend = Xinit + X[:, 1] - 1.0
        return np.stack([Xinit, Xend], axis=-1)
    elif mthd == 'b2c':
        Xc = np.round(0.5*(X[:, 0] + X[:, 1]))
        d = X[:, 1] - X[:, 0] + 1.0
        return np.stack([Xc, d], axis=-1)
    elif mthd == 'd2b':
        Xinit = X[:, 0]
        Xend = X[:, 0] + X[:, 1] - 1.0
        return np.stack([Xinit, Xend], axis=-1)


def segment_intersection(target_segments, test_segments,
                         return_ratio_target=False):
    """Compute intersection btw segments

    Parameters
    ----------
    target_segments : ndarray
        2-dim array in format [m x 2:=[init, end]]
    test_segments : ndarray
        2-dim array in format [n x 2:=[init, end]]
    return_ratio_target : bool, optional
        extra ndarray output with ratio btw size of intersection over size of
        target-segments

    Outputs
    -------
    intersect : ndarray
        3-dim array in format [m, n, 2:=[init, end]]
    ratio_target : ndarray
        2-dim array [m x n] with ratio btw size of intersect over size of
        target segment

    Note: It assumes that target-segments are more scarce that test-segments

    """
    if target_segments.ndim != 2 or test_segments.ndim != 2:
        raise ValueError('Dimension of arguments is incorrect')
    m, n = target_segments.shape[0], test_segments.shape[0]
    if return_ratio_target:
        ratio_target = np.zeros((m, n))

    intersect = np.zeros((m, n, 2))
    for i in xrange(m):
        target_size = target_segments[i, 1] - target_segments[i, 0] + 1.0
        tt1 = np.maximum(target_segments[i, 0], test_segments[:, 0])
        tt2 = np.minimum(target_segments[i, 1], test_segments[:, 1])

        intersect[i, :, 0], intersect[i, :, 1] = tt1, tt2
        if return_ratio_target:
            isegs_size = (tt2 - tt1 + 1.0).clip(0)
            ratio_target[i, :] = isegs_size / target_size

    if return_ratio_target:
        return intersect, ratio_target
    return intersect


def segment_iou(target_segments, test_segments):
    """Compute intersection over union btw segments

    Parameters
    ----------
    target_segments : ndarray
        2-dim array in format [m x 2:=[init, end]]
    test_segments : ndarray
        2-dim array in format [n x 2:=[init, end]]

    Outputs
    -------
    iou : ndarray
        2-dim array [m x n] with IOU ratio.

    Note: It assumes that target-segments are more scarce that test-segments

    """
    if target_segments.ndim != 2 or test_segments.ndim != 2:
        raise ValueError('Dimension of arguments is incorrect')

    m, n = target_segments.shape[0], test_segments.shape[0]
    iou = np.empty((m, n))
    for i in xrange(m):
        tt1 = np.maximum(target_segments[i, 0], test_segments[:, 0])
        tt2 = np.minimum(target_segments[i, 1], test_segments[:, 1])

        # Non-negative overlap score
        intersection = (tt2 - tt1 + 1.0).clip(0)
        union = ((test_segments[:, 1] - test_segments[:, 0] + 1) +
                 (target_segments[i, 1] - target_segments[i, 0] + 1) -
                 intersection)
        # Compute overlap as the ratio of the intersection
        # over union of two segments at the frame level.
        iou[i, :] = intersection / union
    return iou


def segment_unit_scaling(X, T, init=None, copy=False):
    """Scale segments onto a unit reference scale [0, 1]

    Parameters
    ----------
    X : ndarray
        [n x 2] array with temporal annotations in [center, duration] format
    T : int, optional
        duration
    init : ndarray
        [n] array with initial value of temporal reference

    Outputs
    -------
    Y : ndarray
        [n x 2] array with transformed temporal annotations

    """
    if X.ndim != 2:
        raise ValueError('Incorrect number of dimension on X')
    Y = X
    if copy:
        Y = X.copy()

    if init is not None:
        if init.size != Y.shape[0]:
            raise ValueError('Incompatible reference, init')
        Y[:, 0] -= init

    Y[:, 0] /= T - 1
    Y[:, 1] /= T
    return Y


# String utilities
def levenshtein_distance(s1, s2):
    """Compute Levenshtein distance btw two strings

    Note
    ----
    Taken from wikibooks.org-wiki-Algorithm_Implementation

    """
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    # len(s1) >= len(s2)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            # j+1 instead of j since previous_row and current_row are one
            # character longer than s2
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


# IO utilities
def dump_json(filename, data, **kwargs):
    """Serialize data as JSON-file

    Parameters
    ----------
    filename : str
    data : list, dict, etc.
        Data to save. Chech json.dump for more details and description.

    """
    with open(filename, 'w') as f:
        json.dump(data, f, **kwargs)
    return None


def file_as_folder(filename):
    """Return a filename ending with os-path-separator

    Parameters
    ----------
    filename : str
        Fullpath filename

    Outputs
    -------
    filename : str
        Fullpath filename ending with os-path-separator

    """
    return os.path.splitext(filename)[0] + os.path.sep
