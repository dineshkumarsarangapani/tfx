# Iris TFX pipeline template

This template demonstrates the end-to-end workflow and steps of how to
classify Iris flower subspecies.

Please see [Create a TFX pipeline for your data with Iris template](
https://github.com/tensorflow/tfx/blob/master/docs/tutorials/tfx/template.ipynb)
tutorial to learn how to use this template.

Use `--model iris` when copying the template. For example,
```
tfx template copy \
   --pipeline_name="${PIPELINE_NAME}" \
   --destination_path="${PROJECT_DIR}" \
   --model=iris
```

This template doesn't support BigQueryExampleGen, so it needs to be skipped.


## The dataset

This template uses the [Iris dataset](
https://archive.ics.uci.edu/ml/datasets/iris), which provides a working example
to help you get started.  Once you have worked through the example, you should
modify the code to use your own preprocessing, model, and dataset.


## Content of the template

The template generates three different kinds of Python code which are needed to
run a TFX pipeline.

- `pipeline` directory contains *a pipeline definition* which specifies what
  components to use and how the components will be interconnected.
  It also contains various config variables to run the pipeline.
- `models` directory contains an ML model definition, which is required by the
  `Trainer`, `Transform`, and `Tuner` components.
- The last piece is a platform specific configuration that describes physical
  paths and orchestrators. Currently we have `local_runner.py` and
  `kubeflow_runner.py`. These files are located at the root of the project
  directory.
