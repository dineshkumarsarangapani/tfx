# Copyright 2020 Google LLC. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Portable library for output artifacts resolution including caching decision."""

import collections
import os
from typing import Dict, List, Text

from absl import logging

from tfx import types
from tfx.dsl.io import fileio
from tfx.proto.orchestration import pipeline_pb2
from tfx.types import artifact_utils
from tfx.types.value_artifact import ValueArtifact

_EXECUTION = 'execution'
_STATEFUL_WORKING_DIR = 'stateful_working_dir'
_EXECUTION_OUTPUT_FILE = 'executor_output.pb'
_VALUE_ARTIFACT_FILE_NAME = 'value'


def make_output_dirs(output_dict: Dict[Text, List[types.Artifact]]) -> None:
  """Make dirs for output artifacts' URI."""
  for _, artifact_list in output_dict.items():
    for artifact in artifact_list:
      if isinstance(artifact, ValueArtifact):
        # If it is a ValueArtifact, create a file.
        artifact_dir = os.path.dirname(artifact.uri)
        fileio.makedirs(artifact_dir)
        with fileio.open(artifact.uri, 'w') as f:
          # Because fileio.open won't create an empty file, we write an
          # empty string to it to force the creation.
          f.write('')
      else:
        # Otherwise create a dir.
        fileio.makedirs(artifact.uri)


def remove_output_dirs(output_dict: Dict[Text, List[types.Artifact]]) -> None:
  """Remove dirs of output artifacts' URI."""
  for _, artifact_list in output_dict.items():
    for artifact in artifact_list:
      if fileio.isdir(artifact.uri):
        fileio.rmtree(artifact.uri)
      else:
        fileio.remove(artifact.uri)


class OutputsResolver:
  """This class has methods to handle launcher output related logic."""

  def __init__(self, pipeline_node: pipeline_pb2.PipelineNode,
               pipeline_info: pipeline_pb2.PipelineInfo,
               pipeline_runtime_spec: pipeline_pb2.PipelineRuntimeSpec):
    self._pipeline_node = pipeline_node
    self._pipeline_info = pipeline_info
    self._pipeline_root = (
        pipeline_runtime_spec.pipeline_root.field_value.string_value)
    self._pipeline_run_id = (
        pipeline_runtime_spec.pipeline_run_id.field_value.string_value)
    self._node_dir = os.path.join(
        self._pipeline_root,
        pipeline_node.node_info.id)

  def generate_output_artifacts(
      self, execution_id: int) -> Dict[Text, List[types.Artifact]]:
    """Generates output artifacts given execution_id."""
    output_artifacts = collections.defaultdict(list)
    for key, output_spec in self._pipeline_node.outputs.outputs.items():
      artifact = artifact_utils.deserialize_artifact(
          output_spec.artifact_spec.type)
      artifact.uri = os.path.join(self._node_dir, key, str(execution_id))
      if isinstance(artifact, ValueArtifact):
        artifact.uri = os.path.join(artifact.uri, _VALUE_ARTIFACT_FILE_NAME)
      # artifact.name will contain the set of information to track its creation
      # and is guaranteed to be idempotent across retires of a node.
      artifact.name = '{0}:{1}:{2}:{3}:{4}'.format(
          self._pipeline_info.id,
          self._pipeline_run_id,
          self._pipeline_node.node_info.id,
          key,
          # The index of this artifact, since we only has one artifact per
          # output for now, it is always 0.
          # TODO(b/162331170): Update the "0" to the actual index.
          0)
      logging.debug('Creating output artifact uri %s as directory',
                    artifact.uri)
      output_artifacts[key].append(artifact)

    return output_artifacts

  def get_executor_output_uri(self, execution_id: int):
    """Generates executor output uri given execution_id."""
    execution_dir = os.path.join(self._node_dir,
                                 _EXECUTION, str(execution_id))
    fileio.makedirs(execution_dir)
    executor_output_uri = os.path.join(execution_dir, _EXECUTION_OUTPUT_FILE)
    return executor_output_uri

  def get_stateful_working_directory(self):
    """Generates stateful working directory given execution id."""
    # TODO(b/150979622): We should introduce an id that is not changed across
    # retires of the same component run to provide better isolation between
    # "retry" and "new execution". When it is available, introduce it into
    # statuful working direcotry.
    stateful_working_dir = os.path.join(self._node_dir,
                                        self._pipeline_run_id,
                                        _STATEFUL_WORKING_DIR)
    fileio.makedirs(stateful_working_dir)
    return stateful_working_dir
