# Lint as: python2, python3
# Copyright 2019 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Python source file include Iris pipeline functions and necessary utils.

The utilities in this file are used to build a model with native Keras.
This module file will be used in Transform and generic Trainer.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
from typing import List, Text
import absl
import kerastuner
import tensorflow as tf
from tensorflow import keras
from tensorflow_cloud import CloudTuner
import tensorflow_model_analysis as tfma
import tensorflow_transform as tft
from tensorflow_transform.tf_metadata import schema_utils

from tfx.components.trainer.executor import TrainerFnArgs
from tfx.components.trainer.fn_args_utils import FnArgs
from tfx.components.tuner.component import TunerFnResult

_PROJECT_ID = 'my-gcp-project'
_REGION = 'us-central1'

_FEATURE_KEYS = ['sepal_length', 'sepal_width', 'petal_length', 'petal_width']
_LABEL_KEY = 'variety'

# Iris dataset has 150 records, and is divided to train and eval splits in 2:1
# ratio.
_TRAIN_DATA_SIZE = 100
_EVAL_DATA_SIZE = 50
_TRAIN_BATCH_SIZE = 20
_EVAL_BATCH_SIZE = 10


def _transformed_name(key):
  return key + '_xf'


# Tf.Transform considers these features as "raw"
def _get_raw_feature_spec(schema):
  return schema_utils.schema_as_feature_spec(schema).feature_spec


def _gzip_reader_fn(filenames):
  """Small utility returning a record reader that can read gzip'ed files."""
  return tf.data.TFRecordDataset(filenames, compression_type='GZIP')


def _serving_input_receiver_fn(tf_transform_output, schema):
  """Build the serving inputs.

  Args:
    tf_transform_output: A TFTransformOutput.
    schema: the schema of the input data.

  Returns:
    Tensorflow graph which parses examples, applying tf-transform to them.
  """
  raw_feature_spec = _get_raw_feature_spec(schema)
  raw_feature_spec.pop(_LABEL_KEY)

  raw_input_fn = tf.estimator.export.build_parsing_serving_input_receiver_fn(
      raw_feature_spec, default_batch_size=None)
  serving_input_receiver = raw_input_fn()

  transformed_features = tf_transform_output.transform_raw_features(
      serving_input_receiver.features)

  return tf.estimator.export.ServingInputReceiver(
      transformed_features, serving_input_receiver.receiver_tensors)


def _eval_input_receiver_fn(tf_transform_output, schema):
  """Build the evalution inputs for the tf-model-analysis to run the model.

  Args:
    tf_transform_output: A TFTransformOutput.
    schema: the schema of the input data.

  Returns:
    EvalInputReceiver function, which contains:
      - Tensorflow graph which parses raw untransformed features, applies the
        tf-transform preprocessing operators.
      - Transformed features as serialized tf.Examples.
      - Labels
  """
  # Notice that the inputs are raw features, not transformed features here.
  raw_feature_spec = _get_raw_feature_spec(schema)

  serialized_tf_example = tf.compat.v1.placeholder(
      dtype=tf.string, shape=[None], name='input_example_tensor')

  # Add a parse_example operator to the tensorflow graph, which will parse
  # raw, untransformed, tf examples.
  features = tf.io.parse_example(
      serialized=serialized_tf_example, features=raw_feature_spec)

  # Now that we have our raw examples, process them through the tf-transform
  # function computed during the preprocessing step.
  transformed_features = tf_transform_output.transform_raw_features(
      features)

  # The key name MUST be 'examples'.
  receiver_tensors = {'examples': serialized_tf_example}

  # NOTE: Model is driven by transformed features (since training works on the
  # materialized output of TFT, but slicing will happen on raw features.
  features.update(transformed_features)

  return tfma.export.EvalInputReceiver(
      features=features,
      receiver_tensors=receiver_tensors,
      labels=transformed_features[_transformed_name(_LABEL_KEY)])


def _get_serve_tf_examples_fn(model, tf_transform_output):
  """Returns a function that parses a serialized tf.Example."""

  model.tft_layer = tf_transform_output.transform_features_layer()

  @tf.function
  def serve_tf_examples_fn(serialized_tf_examples):
    """Returns the output to be used in the serving signature."""
    feature_spec = tf_transform_output.raw_feature_spec()
    feature_spec.pop(_LABEL_KEY)
    parsed_features = tf.io.parse_example(serialized_tf_examples, feature_spec)

    transformed_features = model.tft_layer(parsed_features)

    return model(transformed_features)

  return serve_tf_examples_fn


def _input_fn(file_pattern: List[Text],
              tf_transform_output: tft.TFTransformOutput,
              batch_size: int = 200) -> tf.data.Dataset:
  """Generates features and label for tuning/training.

  Args:
    file_pattern: List of paths or patterns of input tfrecord files.
    tf_transform_output: A TFTransformOutput.
    batch_size: representing the number of consecutive elements of returned
      dataset to combine in a single batch

  Returns:
    A dataset that contains (features, indices) tuple where features is a
      dictionary of Tensors, and indices is a single Tensor of label indices.
  """
  transformed_feature_spec = (
      tf_transform_output.transformed_feature_spec().copy())

  dataset = tf.data.experimental.make_batched_features_dataset(
      file_pattern=file_pattern,
      batch_size=batch_size,
      features=transformed_feature_spec,
      reader=_gzip_reader_fn,
      label_key=_transformed_name(_LABEL_KEY))

  return dataset


def _get_hyperparameters() -> kerastuner.HyperParameters:
  """Returns hyperparameters for building Keras model."""
  hp = kerastuner.HyperParameters()
  # Defines search space.
  hp.Choice('learning_rate', [1e-5, 1e-4, 1e-3, 1e-2], default=1e-2)
  hp.Int('num_layers', 1, 4, default=2)
  return hp


def _build_keras_model(hparams: kerastuner.HyperParameters) -> tf.keras.Model:
  """Creates a DNN Keras model for classifying iris data.

  Args:
    hparams: Holds HyperParameters for tuning.

  Returns:
    A Keras Model.
  """
  # The model below is built with Functional API, please refer to
  # https://www.tensorflow.org/guide/keras/overview for all API options.
  inputs = [
      keras.layers.Input(shape=(1,), name=_transformed_name(f))
      for f in _FEATURE_KEYS
  ]
  d = keras.layers.concatenate(inputs)
  for _ in range(int(hparams.get('num_layers'))):
    d = keras.layers.Dense(8, activation='relu')(d)
  outputs = keras.layers.Dense(3, activation='softmax')(d)

  model = keras.Model(inputs=inputs, outputs=outputs)
  model.compile(
      optimizer=keras.optimizers.Adam(hparams.get('learning_rate')),
      loss='sparse_categorical_crossentropy',
      metrics=[keras.metrics.SparseCategoricalAccuracy()])

  model.summary(print_fn=absl.logging.info)
  return model


# TFX Transform will call this function.
def preprocessing_fn(inputs):
  """tf.transform's callback function for preprocessing inputs.

  Args:
    inputs: map from feature keys to raw not-yet-transformed features.

  Returns:
    Map from string feature key to transformed feature operations.
  """
  outputs = {}

  for key in _FEATURE_KEYS:
    outputs[_transformed_name(key)] = tft.scale_to_z_score(inputs[key])
  # TODO(b/157064428): Support label transformation for Keras.
  # Do not apply label transformation as it will result in wrong evaluation.
  outputs[_transformed_name(_LABEL_KEY)] = inputs[_LABEL_KEY]

  return outputs


# TFX Tuner will call this function.
def tuner_fn(fn_args: FnArgs) -> TunerFnResult:
  """Build the tuner using the CloudTuner API.

  Args:
    fn_args: Holds args as name/value pairs.
      - working_dir: working dir for tuning.
      - train_files: List of file paths containing training tf.Example data.
      - eval_files: List of file paths containing eval tf.Example data.
      - train_steps: number of train steps.
      - eval_steps: number of eval steps.
      - schema_path: optional schema of the input data.
      - transform_graph_path: optional transform graph produced by TFT.

  Returns:
    A namedtuple contains the following:
      - tuner: A BaseTuner that will be used for tuning.
      - fit_kwargs: Args to pass to tuner's run_trial function for fitting the
                    model , e.g., the training and validation dataset. Required
                    args depend on the above tuner's implementation.
  """
  # CloudTuner is a subclass of kerastuner.Tuner which inherits from
  # BaseTuner.
  tuner = CloudTuner(
      _build_keras_model,
      project_id=_PROJECT_ID,
      region=_REGION,
      objective=kerastuner.Objective('val_sparse_categorical_accuracy', 'max'),
      hyperparameters=_get_hyperparameters(),
      max_trials=8,
      directory=fn_args.working_dir
      )

  transform_graph = tft.TFTransformOutput(fn_args.transform_graph_path)
  train_dataset = _input_fn(fn_args.train_files, transform_graph)
  eval_dataset = _input_fn(fn_args.eval_files, transform_graph)
  return TunerFnResult(
      tuner=tuner,
      fit_kwargs={
          'x': train_dataset,
          'validation_data': eval_dataset,
          'steps_per_epoch': fn_args.train_steps,
          'validation_steps': fn_args.eval_steps
      })


# TFX Trainer will call this function.
def trainer_fn(trainer_fn_args, schema):
  """Build the estimator using the high level API.

  Args:
    trainer_fn_args: Holds args used to train the model as name/value pairs.
    schema: Holds the schema of the training examples.

  Returns:
    A dict of the following:
      - estimator: The estimator that will be used for training and eval.
      - train_spec: Spec for training.
      - eval_spec: Spec for eval.
      - eval_input_receiver_fn: Input function for eval.
  """

  train_batch_size = 20
  eval_batch_size = 10

  tf_transform_output = tft.TFTransformOutput(trainer_fn_args.transform_output)

  train_input_fn = lambda: _input_fn(  # pylint: disable=g-long-lambda
      trainer_fn_args.train_files,
      tf_transform_output,
      batch_size=train_batch_size)

  eval_input_fn = lambda: _input_fn(  # pylint: disable=g-long-lambda
      trainer_fn_args.eval_files,
      tf_transform_output,
      batch_size=eval_batch_size)

  train_spec = tf.estimator.TrainSpec(
      train_input_fn, max_steps=trainer_fn_args.train_steps)

  serving_receiver_fn = lambda: _serving_input_receiver_fn(  # pylint: disable=g-long-lambda
      tf_transform_output, schema)

  exporter = tf.estimator.FinalExporter('iris', serving_receiver_fn)
  eval_spec = tf.estimator.EvalSpec(
      eval_input_fn,
      steps=trainer_fn_args.eval_steps,
      exporters=[exporter],
      name='iris-eval')

  run_config = tf.estimator.RunConfig(
      save_checkpoints_steps=999, keep_checkpoint_max=1)

  run_config = run_config.replace(model_dir=trainer_fn_args.serving_model_dir)

  estimator = tf.keras.estimator.model_to_estimator(
      keras_model=_build_keras_model(
          kerastuner.HyperParameters.from_config(
              trainer_fn_args.hyperparameters)),
      config=run_config)

  # Create an input receiver for TFMA processing
  eval_receiver_fn = lambda: _eval_input_receiver_fn(tf_transform_output, schema
                                                    )

  return {
      'estimator': estimator,
      'train_spec': train_spec,
      'eval_spec': eval_spec,
      'eval_input_receiver_fn': eval_receiver_fn
  }


# TFX Generic Trainer will call this function.
def run_fn(fn_args: TrainerFnArgs):
  """Train the model based on given args.

  Args:
    fn_args: Holds args used to train the model as name/value pairs.
  """
  tf_transform_output = tft.TFTransformOutput(fn_args.transform_output)

  train_dataset = _input_fn(fn_args.train_files, tf_transform_output,
                            batch_size=_TRAIN_BATCH_SIZE)
  eval_dataset = _input_fn(fn_args.eval_files, tf_transform_output,
                           batch_size=_EVAL_BATCH_SIZE)

  if fn_args.hyperparameters:
    hparams = kerastuner.HyperParameters.from_config(fn_args.hyperparameters)
  else:
    # This is a shown case when hyperparameters is decided and Tuner is removed
    # from the pipeline. User can also inline the hyperparameters directly in
    # _build_keras_model.
    hparams = _get_hyperparameters()
  absl.logging.info('HyperParameters for training: %s' % hparams.get_config())

  mirrored_strategy = tf.distribute.MirroredStrategy()
  with mirrored_strategy.scope():
    model = _build_keras_model(hparams)

  steps_per_epoch = _TRAIN_DATA_SIZE / _TRAIN_BATCH_SIZE

  try:
    log_dir = fn_args.model_run_dir
  except KeyError:
    # TODO(b/158106209): use ModelRun instead of Model artifact for logging.
    log_dir = os.path.join(os.path.dirname(fn_args.serving_model_dir), 'logs')

  # Write logs to path
  tensorboard_callback = tf.keras.callbacks.TensorBoard(
      log_dir=log_dir, update_freq='batch')

  model.fit(
      train_dataset,
      epochs=int(fn_args.train_steps / steps_per_epoch),
      steps_per_epoch=steps_per_epoch,
      validation_data=eval_dataset,
      validation_steps=fn_args.eval_steps,
      callbacks=[tensorboard_callback])

  signatures = {
      'serving_default':
          _get_serve_tf_examples_fn(model,
                                    tf_transform_output).get_concrete_function(
                                        tf.TensorSpec(
                                            shape=[None],
                                            dtype=tf.string,
                                            name='examples')),
  }
  model.save(fn_args.serving_model_dir, save_format='tf', signatures=signatures)
