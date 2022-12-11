# Copyright 2018-2021
# Institute of Neuroscience and Medicine (INM-1), Forschungszentrum Jülich GmbH

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from ..commons import create_key, clear_name, logger

import re


class AtlasConcept:
    """
    Parent class encapsulating commonalities of the basic siibra concept like atlas, parcellation, space, region.
    These concepts have an id, name, and key, and they are bootstrapped from metadata stored in an online resources.
    Typically, they are linked with one or more datasets that can be retrieved from the same or another online resource,
    providing data files or additional metadata descriptions on request.
    """

    def __init__(
        self,
        identifier: str,
        name: str,
        shortname: str = None,
        description: str = None,
        modality: str = "",
        publications: list = [],
        datasets: list = [],
    ):
        """
        Construct a new atlas concept base object.

        Parameters
        ----------
            identifier : str
                Unique identifier of the parcellation
            name : str
                Human-readable name of the parcellation
            shortname: str
                Shortform of human-readable name (optional)
            description: str
                Textual description of the parcellation
            modality  :  str or None
                Specification of the modality underlying this concept
            datasets : list
                list of datasets corresponding to this concept
            publications: list
                List of publications, each a dictionary with "doi" and/or "citation" fields

        """
        self._id = identifier
        self.name = name
        self.shortname = shortname
        self.modality = modality
        self.description = description
        self.publications = publications
        self.datasets = datasets

    @classmethod
    def registry(cls):
        if cls._configuration_folder is None:
            return None
        if cls._registry_cached is None:
            from ..configuration.configuration import Configuration
            from ..commons import InstanceTable
            conf = Configuration()
            # visit the configuration to provide a cleanup function
            # in case the user changes the configuration during runtime.
            Configuration.register_cleanup(cls.clear_registry)
            assert cls._configuration_folder in conf.folders
            objects = conf.build_objects(cls._configuration_folder)
            logger.info(f"Built {len(objects)} preconfigured {cls.__name__} objects.")
            assert len(objects) > 0
            cls._registry_cached = InstanceTable(
                elements={o.key: o for o in objects},
                matchfunc=objects[0].__class__.match
            )
        return cls._registry_cached

    @classmethod
    def clear_registry(cls):
        cls._registry_cached = None

    @classmethod
    def get_instance(cls, spec: str):
        """
        Returns an instance of this class matching the given specification
        from its registry, if possible, otherwise None.
        """
        if cls.registry() is not None:
            return cls.registry().get(spec)

    @property
    def id(self):
        # allows derived classes to assign the id dynamically
        return self._id

    @property
    def key(self):
        return create_key(self.name)

    def __init_subclass__(cls, configuration_folder: str = None):
        """
        This method is called whenever AtlasConcept gets subclassed
        (see https://docs.python.org/3/reference/datamodel.html)
        """
        cls._registry_cached = None
        cls._configuration_folder = configuration_folder
        return super().__init_subclass__()

    def __str__(self):
        return f"{self.__class__.__name__}: {self.name}"

    def matches(self, spec):
        """
        Test if the given specification matches the name, key or id of the concept.
        """
        if isinstance(spec, self.__class__) and (spec == self):
            return True
        elif isinstance(spec, str):
            if spec == self.key:
                return True
            elif spec == self.id:
                return True
            else:
                # match the name
                words = [w for w in re.split("[ -]", spec)]
                squeezedname = clear_name(self.name.lower()).replace(" ", "")
                return any(
                    [
                        all(w.lower() in squeezedname for w in words),
                        spec.replace(" ", "") in squeezedname,
                    ]
                )
        return False

    @classmethod
    def match(cls, obj, spec):
        """Match a given object specification. """
        assert isinstance(obj, cls)
        return obj.matches(spec)
