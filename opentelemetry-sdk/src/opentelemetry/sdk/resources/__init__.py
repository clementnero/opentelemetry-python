# Copyright The OpenTelemetry Authors
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

import abc
import concurrent.futures
import logging
import os
import typing
from json import dumps

import pkg_resources

LabelValue = typing.Union[str, bool, int, float]
Attributes = typing.Dict[str, LabelValue]
logger = logging.getLogger(__name__)


TELEMETRY_SDK_LANGUAGE = "telemetry.sdk.language"
TELEMETRY_SDK_NAME = "telemetry.sdk.name"
TELEMETRY_SDK_VERSION = "telemetry.sdk.version"

OPENTELEMETRY_SDK_VERSION = pkg_resources.get_distribution(
    "opentelemetry-sdk"
).version


class Resource:
    def __init__(self, attributes: Attributes):
        self._attributes = attributes.copy()

    @staticmethod
    def create(attributes: Attributes) -> "Resource":
        if not attributes:
            return _DEFAULT_RESOURCE
        return _DEFAULT_RESOURCE.merge(Resource(attributes))

    @staticmethod
    def create_empty() -> "Resource":
        return _EMPTY_RESOURCE

    @property
    def attributes(self) -> Attributes:
        return self._attributes.copy()

    def merge(self, other: "Resource") -> "Resource":
        merged_attributes = self.attributes
        # pylint: disable=protected-access
        for key, value in other._attributes.items():
            if key not in merged_attributes or merged_attributes[key] == "":
                merged_attributes[key] = value
        return Resource(merged_attributes)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Resource):
            return False
        return self._attributes == other._attributes

    def __hash__(self):
        return hash(dumps(self._attributes, sort_keys=True))


_EMPTY_RESOURCE = Resource({})
_DEFAULT_RESOURCE = Resource(
    {
        TELEMETRY_SDK_LANGUAGE: "python",
        TELEMETRY_SDK_NAME: "opentelemetry",
        TELEMETRY_SDK_VERSION: OPENTELEMETRY_SDK_VERSION,
    }
)


class ResourceDetector(abc.ABC):
    def __init__(self, raise_on_error=False):
        self.raise_on_error = raise_on_error

    @abc.abstractmethod
    def detect(self) -> "Resource":
        raise NotImplementedError()


class OTELResourceDetector(ResourceDetector):
    # pylint: disable=no-self-use
    def detect(self) -> "Resource":
        env_resources_items = os.environ.get("OTEL_RESOURCE_ATTRIBUTES")
        env_resource_map = {}
        if env_resources_items:
            env_resource_map = {
                key.strip(): value.strip()
                for key, value in (
                    item.split("=") for item in env_resources_items.split(",")
                )
            }
        return Resource(env_resource_map)


def get_aggregated_resources(
    detectors: typing.List["ResourceDetector"],
    initial_resource: typing.Optional[Resource] = None,
    timeout=5,
) -> "Resource":
    """ Retrieves resources from detectors in the order that they were passed

    :param detectors: List of resources in order of priority
    :param initial_resource: Static resource. This has highest priority
    :param timeout: Number of seconds to wait for each detector to return
    :return:
    """
    final_resource = initial_resource or _EMPTY_RESOURCE
    detectors = [OTELResourceDetector()] + detectors

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(detector.detect) for detector in detectors]
        for detector_ind, future in enumerate(futures):
            detector = detectors[detector_ind]
            try:
                detected_resources = future.result(timeout=timeout)
            # pylint: disable=broad-except
            except Exception as ex:
                if detector.raise_on_error:
                    raise ex
                logger.warning(
                    "Exception %s in detector %s, ignoring", ex, detector
                )
                detected_resources = _EMPTY_RESOURCE
            finally:
                final_resource = final_resource.merge(detected_resources)
    return final_resource
