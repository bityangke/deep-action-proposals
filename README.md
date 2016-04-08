# Deep Action Proposals
Action Proposals generated by deep models

## Installation

We use conda for deployment. You can create an evironment for this project with the `environment.yml`.

*Dependencies:* gcc, CUDA, conda.

## Contributitions

We are welcome to contributions, please keep your pull-request and simple and with unit-test so we can go back to you as soon as we can.

Please run all the test before submitting it.

```
py.test daps/test/; py.test daps/utils/test/; for i in $(ls daps/*.py); do $(python -c "import daps.$(basename $i .py)"); done; for i in $(ls daps/tools/); do "daps/tools/"$i -h; done | grep "error";
```

*Disclaimer:* we lack of official maintainer. Therefore, adoptions may be slower. Please be patient :sweat_smile:

## Data Flow Recipes

What do you want to do?

1. Detect proposals with a pre-trained model. Check this recipe.

   1. [Extract C3D](#c3d-extraction)

   2. Use `daps.model.retrieve_proposals`

2. Train my own model.

   1. [Organize your data](#interacting-with-datasets).

   2. [Extract C3D](#c3d-extraction).

   Optional, perform PCA.

   3. Create dataset of segments

      1. Generate segments around annotations. `tools/compute_priors.py` gives you hints for doing that.

      2. Dump a HDF5 file for these segments. `tools/create_dataset.py` gives you hints for doing that.

    4. You are ready to learn a DAPs follow instructions on `python daps/learning.py`.

    *Disclaimer:* evaluation of the DAPs during learning is perform at the level of proposal localization per segment.

### Interacting with Datasets

Our models is trained on untrimmed labeled datasets. In short, we recommend you to check `daps.dataset.Thumos14` and write your own class to pack all data of your dataset in our format.

Keep in mind that we use a folder called `metadata` inside each root folder of the dataset to allocate cached relevant information of each dataset as CSV files.
These CSV files contain information about video names, videdo duration (seconds), frame rate, number of video frames, instance location, etc.
An example of this folder is [here](example/metadata/)

[Go back](#data-flow-recipes)

### C3D extraction

0. Compile C3D code. Recommended optional:symlink root-folder in `3rdparty/`.

1. Dump frames of videos.

   > Why? we choose raw-images over raw-videos because weird behaviors happened using the video interface. Maybe, your opencv-ffmpeg version is not appropiate.

2. Create CSV for using `daps.utils.c3d.input_file_generator`.

3. Generate input.c3d and output.c3d with `daps.utils.c3d.input_file_generator(csv_from_step2)`.

4. Check that CSV generate in the step before point to a correct path.

   > Tip: If you need to modify them, you may want to do it in [bash](3rdparty/README.md#useful-bash-commands).

5. Use our python program `tools/c3d_feat_frm.py` to extract c3d features. For example:

   ```
   python/tools/c3d_feat_frm.py data/models/conv3d_deepnetA_sport1m_iter_1900000.caffemodel output.c3d fc7-1 -c '{"seq_source": "input.c3d", "mean_file": "data/models/sport1m_train16_128_mean.binaryproto", "batch_size": 50, "use_image": "true"}' -v
   ```

6. Once you extract C3D codes of your videos, you should save it as HDF5. We save a `Group` for each video and one `Dataset` with C3D features on each group.
The name of each `Group` corresponds to the video-id, while the name used for `Dataset` is `c3d-features`. Do not forget to set `daps.c3d_encoder.Feature` accordingly to your stride and pooling strategy.

[Go back](#data-flow-recipes)
