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
"""Placeholders represent not-yet-available values at the component authoring time."""

import abc
from typing import cast
from typing import Union

from tfx.proto.orchestration import placeholder_pb2


def input(key: str) -> 'Placeholder':  # pylint: disable=redefined-builtin
  """Returns a Placeholder that represents an input artifact.

  Args:
    key: The key of the input artifact.

  Returns:
    A Placeholder that supports
      1. Rendering the whole artifact as text_format.
         Example: input('model')
      2. Accessing a specific index using [index], if multiple artifacts are
         associated with the given key.
         Example: input('model')[0]
      3. Getting the URI of an artifact through .uri property.
         Example: input('model').uri or input('model')[0].uri
      4. Getting the URI of a specific split of an artifact using
         .split_uri(split_name) method.
         Example: input('examples')[0].split_uri('train')
      5. Getting the value of a primitive artifact through .value property.
         Example: input('primitive').value
      6. Concatenating with other placeholders or strings.
         Example: input('model').uri + '/model/' + exec_property('version')
  """
  return ArtifactPlaceholder(placeholder_pb2.Placeholder.Type.INPUT_ARTIFACT,
                             key)


def output(key: str) -> 'Placeholder':
  """Returns a Placeholder that represents an output artifact.

  It is the same as input(...) function, except it is for output artifacts.

  Args:
    key: The key of the output artifact.

  Returns:
    A Placeholder that supports
      1. Rendering the whole artifact as text_format.
         Example: output('model')
      2. Accessing a specific index using [index], if multiple artifacts are
         associated with the given key.
         Example: output('model')[0]
      3. Getting the URI of an artifact through .uri property.
         Example: output('model').uri or output('model')[0].uri
      4. Getting the URI of a specific split of an artifact using
         .split_uri(split_name) method.
         Example: output('examples')[0].split_uri('train')
      5. Getting the value of a primitive artifact through .value property.
         Example: output('primitive').value
      6. Concatenating with other placeholders or strings.
         Example: output('model').uri + '/model/' + exec_property('version')
  """
  return ArtifactPlaceholder(placeholder_pb2.Placeholder.Type.OUTPUT_ARTIFACT,
                             key)


def exec_property(key: str) -> 'Placeholder':
  """Returns a Placeholder that represents an execution property.

  Args:
    key: The key of the output artifact.

  Returns:
    A Placeholder that supports
      1. Rendering the value of an execution property at a given key.
         Example: exec_property('version')
      2. Rendering the whole proto or a proto field of an execution property,
         if the value is a proto type.
         The (possibly nested) proto field in a placeholder can be accessed as
         if accessing a proto field in Python. Only dot notation is supported,
         accessing map or repeated fields using brackets is not supported.
         Example: exec_property('model_config').num_layers
      3. Concatenating with other placeholders or strings.
         Example: output('model').uri + '/model/' + exec_property('version')
  """
  return ExecPropertyPlaceholder(key)


class _PlaceholderOperator(abc.ABC):
  """An Operator performs an operation on a Placeholder.

  It knows how to encode itself into a proto.
  """

  def __init__(self):
    pass

  @abc.abstractmethod
  def encode(
      self, sub_expression_pb: placeholder_pb2.PlaceholderExpression
  ) -> placeholder_pb2.PlaceholderExpression:
    pass


class _ArtifactUriOperator(_PlaceholderOperator):
  """Artifact URI Operator extracts the URI from an artifact Placeholder.

  Prefer to use the .uri property of ArtifactPlaceholder.
  """

  def __init__(self, split: str = ''):
    super().__init__()
    self._split = split

  def encode(
      self, sub_expression_pb: placeholder_pb2.PlaceholderExpression
  ) -> placeholder_pb2.PlaceholderExpression:
    expression_pb = placeholder_pb2.PlaceholderExpression()
    expression_pb.operator.artifact_uri_op.expression.CopyFrom(
        sub_expression_pb)
    if self._split:
      expression_pb.operator.artifact_uri_op.split = self._split
    return expression_pb


class _ArtifactValueOperator(_PlaceholderOperator):
  """Artifact Value Operator extracts the value from a primitive artifact Placeholder.

  Prefer to use the .value property of ArtifactPlaceholder.
  """

  def encode(
      self, sub_expression_pb: placeholder_pb2.PlaceholderExpression
  ) -> placeholder_pb2.PlaceholderExpression:
    expression_pb = placeholder_pb2.PlaceholderExpression()
    expression_pb.operator.artifact_value_op.expression.CopyFrom(
        sub_expression_pb)
    return expression_pb


class _IndexOperator(_PlaceholderOperator):
  """Index Operator extracts value at the given index of a Placeholder.

  Prefer to use [index] operator overloading of Placeholder.
  """

  def __init__(self, index: int):
    super().__init__()
    self._index = index

  def encode(
      self, sub_expression_pb: placeholder_pb2.PlaceholderExpression
  ) -> placeholder_pb2.PlaceholderExpression:
    expression_pb = placeholder_pb2.PlaceholderExpression()
    expression_pb.operator.index_op.expression.CopyFrom(sub_expression_pb)
    expression_pb.operator.index_op.index = self._index
    return expression_pb


class _ConcatOperator(_PlaceholderOperator):
  """Concat Operator concatenates multiple Placeholders.

  Prefer to use + operator overloading of Placeholder.
  """

  def __init__(self, other: Union[str, 'Placeholder']):
    super().__init__()
    self._other = other

  def encode(
      self, sub_expression_pb: placeholder_pb2.PlaceholderExpression
  ) -> placeholder_pb2.PlaceholderExpression:
    # ConcatOperator's proto version contains multiple placeholder expressions
    # as operands. For convenience, the Python version is implemented taking
    # only two operands.
    #
    # Resolve other expression
    if isinstance(self._other, Placeholder):
      other_expression = cast(Placeholder, self._other)
      other_expression_pb = other_expression.encode()
    else:
      other_expression_pb = placeholder_pb2.PlaceholderExpression()
      other_expression_pb.value.string_value = self._other

    # Try combining with existing concat operator
    if sub_expression_pb.HasField(
        'operator') and sub_expression_pb.operator.HasField('concat_op'):
      sub_expression_pb.operator.concat_op.expressions.append(
          other_expression_pb)
      return sub_expression_pb
    else:
      expression_pb = placeholder_pb2.PlaceholderExpression()
      expression_pb.operator.concat_op.expressions.extend(
          [sub_expression_pb, other_expression_pb])
      return expression_pb


class _ProtoOperator(_PlaceholderOperator):
  """Proto Operator concatenates multiple Placeholders.

  Prefer to use . operator overloading of ExecPropertyPlaceholder.
  """

  def __init__(self, proto_field_path: str):
    super().__init__()
    self._proto_field_path = proto_field_path

  def append_field_path(self, extra_path: str):
    self._proto_field_path += '.' + extra_path

  def encode(
      self, sub_expression_pb: placeholder_pb2.PlaceholderExpression
  ) -> placeholder_pb2.PlaceholderExpression:
    expression_pb = placeholder_pb2.PlaceholderExpression()
    expression_pb.operator.proto_op.expression.CopyFrom(sub_expression_pb)
    expression_pb.operator.proto_op.proto_field_path = self._proto_field_path
    return expression_pb


class Placeholder(abc.ABC):
  """A Placeholder represents not-yet-available values at the component authoring time."""

  def __init__(self, placeholder_type: placeholder_pb2.Placeholder.Type,
               key: str):
    self._operators = []
    self._type = placeholder_type
    self._key = key

  def __getitem__(self, key: int):
    self._operators.append(_IndexOperator(key))
    return self

  def __add__(self, other: Union[str, 'Placeholder']):
    self._operators.append(_ConcatOperator(other))
    return self

  def encode(self) -> placeholder_pb2.PlaceholderExpression:
    expression_pb = placeholder_pb2.PlaceholderExpression()
    expression_pb.placeholder.type = self._type
    expression_pb.placeholder.key = self._key
    for op in self._operators:
      expression_pb = op.encode(expression_pb)
    return expression_pb


class ArtifactPlaceholder(Placeholder):
  """Artifact Placeholder represents an input or an output artifact.

  Prefer to use input(...) or output(...) to create artifact placeholders.
  """

  @property
  def uri(self):
    self._operators.append(_ArtifactUriOperator())
    return self

  def split_uri(self, split: str):
    self._operators.append(_ArtifactUriOperator(split))
    return self

  @property
  def value(self):
    self._operators.append(_ArtifactValueOperator())
    return self


class ExecPropertyPlaceholder(Placeholder):
  """ExecProperty Placeholder represents an execution property.

  Prefer to use exec_property(...) to create exec property placeholders.
  """

  def __init__(self, key: str):
    super().__init__(placeholder_pb2.Placeholder.Type.EXEC_PROPERTY, key)

  def __getattr__(self, field_name: str):
    if self._operators and isinstance(self._operators[-1], _ProtoOperator):
      self._operators[-1].append_field_path(field_name)
    else:
      self._operators.append(_ProtoOperator(field_name))
    return self
