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


from .concept import AtlasConcept
from ..locations import Point, BoundingBox

from ..commons import logger

from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..volumes import volume

class Space(AtlasConcept, configuration_folder="spaces"):
    """
    A particular brain reference space.
    """

    def __init__(
        self,
        identifier: str,
        name: str,
        volumes: List['volume.Volume'] = [],
        shortname: str = "",
        description: str = "",
        modality: str = "",
        publications: list = [],
        datasets: list = [],
    ):
        """
        Constructs a new parcellation object.

        Parameters
        ----------
        identifier : str
            Unique identifier of the space
        name : str
            Human-readable name of the space
        volumes: list of template volumes
        shortname: str
            Shortform of human-readable name (optional)
        description: str
            Textual description of the parcellation
        modality  :  str or None
            Specification of the modality representing this reference space
        publications: list
            List of ssociated publications, each a dictionary with "doi" and/or "citation" fields
        ebrains_ids : dict
            Identifiers of EBRAINS entities corresponding to this Parcellation.
            Key: EBRAINS KG schema, value: EBRAINS KG @id
        """

        AtlasConcept.__init__(
            self,
            identifier=identifier,
            name=name,
            shortname=shortname,
            description=description,
            modality=modality,
            publications=publications,
            datasets=datasets,
        )
        self.volumes = volumes
        for v in self.volumes:
            v.space_info = {"@id": self.id}

    def get_template(self, variant=None, **kwargs):
        """
        Get the volumetric reference template for this space.

        Parameters
        ----------
        variant: str (optional)
            Some templates are provided in different variants, e.g.
            freesurfer is available as either white matter, pial or
            inflated surface for left and right hemispheres (6 variants).
            This field could be used to request a specific variant.
            Per default, the first found variant is returned.

        Yields
        ------
        A VolumeSrc object representing the reference template, or None if not available.
        """
        if variant is None:
            candidates = self.volumes
        else:
            candidates = [v for v in self.volumes if v.name == variant]
        assert len(candidates) > 0
        result = next(iter(candidates))
        if len(candidates) > 1:
            logger.warn(
                f"Multiple volumes/formats available for {self.name}. "
                f"Returning the first, {result.name}, but you could have chosen "
                f"any of {', '.join(f'{v.name}@{fmt}' for v in candidates for fmt in v.formats)}."
            )
        return result

    @property
    def is_surface(self):
        return all(v.is_surface for v in self.volumes)

    def __getitem__(self, slices):
        """
        Get a volume of interest specification from this space.

        Arguments
        ---------
        slices: triple of slice
            defines the x, y and z range
        """
        if len(slices) != 3:
            raise TypeError(
                "Slice access to spaces needs to define x,y and z ranges (e.g. Space[10:30,0:10,200:300])"
            )
        point1 = [0 if s.start is None else s.start for s in slices]
        point2 = [s.stop for s in slices]
        if None in point2:
            # fill upper bounds with maximum physical coordinates
            T = self.get_template()
            shape = Point(T.get_shape(-1), None).transform(T.build_affine(-1))
            point2 = [shape[i] if v is None else v for i, v in enumerate(point2)]
        return self.get_bounding_box(point1, point2)

    def get_bounding_box(self, point1, point2):
        """
        Get a volume of interest specification from this space.

        Arguments
        ---------
        point1: 3D tuple defined in physical coordinates of this reference space
        point2: 3D tuple defined in physical coordinates of this reference space
        """
        return BoundingBox(point1, point2, self)
