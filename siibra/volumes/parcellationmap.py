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

from . import volume as _volume, nifti

from .. import logger, QUIET
from ..commons import MapIndex, MapType, compare_maps, clear_name, create_key, create_gaussian_kernel, Species
from ..core import concept, space, parcellation, region as _region
from ..locations import point, pointset
from ..retrieval import requests

import numpy as np
from tqdm import tqdm
from typing import Union, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.region import Region

from scipy.ndimage.morphology import distance_transform_edt
from collections import defaultdict
from nibabel import Nifti1Image
from nilearn import image
import pandas as pd


class Map(concept.AtlasConcept, configuration_folder="maps"):

    def __init__(
        self,
        identifier: str,
        name: str,
        space_spec: dict,
        parcellation_spec: dict,
        indices: Dict[str, Dict],
        volumes: list = [],
        shortname: str = "",
        description: str = "",
        modality: str = None,
        publications: list = [],
        datasets: list = [],
    ):
        """
        Constructs a new parcellation object.

        Parameters
        ----------
        identifier : str
            Unique identifier of the parcellation
        name : str
            Human-readable name of the parcellation
        space_spec: dict
            Specification of the space (use @id or name fields)
        parcellation_spec: str
            Specification of the parcellation (use @id or name fields)
        indices: dict
            Dictionary of indices for the brain regions.
            Keys are exact region names.
            Per region name, a list of dictionaries with fields "volume" and "label" is expected,
            where "volume" points to the index of the Volume object where this region is mapped,
            and optional "label" is the voxel label for that region.
            For contiuous / probability maps, the "label" can be null or omitted.
            For single-volume labelled maps, the "volume" can be null or omitted.
        volumes: list of Volume
            parcellation volumes
        shortname: str
            Shortform of human-readable name (optional)
        description: str
            Textual description of the parcellation
        modality  :  str or None
            Specification of the modality used for creating the parcellation
        publications: list
            List of ssociated publications, each a dictionary with "doi" and/or "citation" fields
        datasets : list
            datasets associated with this concept
        """
        concept.AtlasConcept.__init__(
            self,
            identifier=identifier,
            name=name,
            species=None,  # inherits species from space
            shortname=shortname,
            description=description,
            publications=publications,
            datasets=datasets,
            modality=modality
        )

        # Since the volumes might include 4D arrays, where the actual
        # volume index points to a z coordinate, we create subvolume
        # indexers from the given volume provider if 'z' is specified.
        self._indices: Dict[str, List[MapIndex]] = {}
        self.volumes: List[_volume.Volume] = []
        remap_volumes = {}
        for regionname, indexlist in indices.items():
            k = clear_name(regionname)
            self._indices[k] = []
            for index in indexlist:
                vol = index.get('volume', 0)
                assert vol in range(len(volumes))
                z = index.get('z')
                if (vol, z) not in remap_volumes:
                    if z is None:
                        self.volumes.append(volumes[vol])
                    else:
                        self.volumes.append(_volume.Subvolume(volumes[vol], z))
                    remap_volumes[vol, z] = len(self.volumes) - 1
                self._indices[k].append(
                    MapIndex(volume=remap_volumes[vol, z], label=index.get('label'), fragment=index.get('fragment'))
                )

        # make sure the indices are unique - each map/label pair should appear at most once
        all_indices = sum(self._indices.values(), [])
        seen = set()
        duplicates = {x for x in all_indices if x in seen or seen.add(x)}
        if len(duplicates) > 0:
            logger.warn(f"Non unique indices encountered in {self}: {duplicates}")

        self._space_spec = space_spec
        self._parcellation_spec = parcellation_spec
        self._affine_cached = None
        for v in self.volumes:
            v._space_spec = space_spec

    @property
    def species(self) -> Species:
        # lazy implementation
        if self._species_cached is None:
            self._species_cached = self.space.species
        return self.space._species_cached

    def get_index(self, region: Union[str, "Region"]):
        """
        Returns the unique index corresponding to the specified region,
        assuming that the specification matches one unique region
        defined in this parcellation map.
        If not unique, or not defined, an exception will be thrown.
        See find_indices() for a less strict search returning all matches.
        """
        matches = self.find_indices(region)
        if len(matches) > 1:
            print(matches)
            raise RuntimeError(
                f"The specification '{region}' matches multiple mapped "
                f"structures in {str(self)}: {list(matches.values())}"
            )
        elif len(matches) == 0:
            raise RuntimeError(
                f"The specification '{region}' does not match to any structure mapped in {self}"
            )
        else:
            return next(iter(matches))

    def find_indices(self, region: Union[str, "Region"]):
        """ Returns the volume/label indices in this map
        which match the given region specification"""
        if region in self._indices:
            return {
                idx: region
                for idx in self._indices[region]
            }
        regionname = region.name if isinstance(region, _region.Region) else region
        matched_region_names = set(_.name for _ in (self.parcellation.find(regionname)))
        matches = matched_region_names & self._indices.keys()
        if len(matches) == 0:
            logger.warn(f"Region {regionname} not defined in {self}")
        return {
            idx: regionname
            for regionname in matches
            for idx in self._indices[regionname]
        }

    def get_region(self, label: int = None, volume: int = None, index: MapIndex = None):
        """ Returns the region mapped by the given index, if any. """
        if index is None:
            index = MapIndex(volume, label)
        matches = [
            regionname
            for regionname, indexlist in self._indices.items()
            if index in indexlist
        ]
        if len(matches) == 0:
            logger.warn(f"Index {index} not defined in {self}")
            return None
        elif len(matches) == 1:
            return self.parcellation.get_region(matches[0])
        else:
            # this should not happen, already tested in constructor
            raise RuntimeError(f"Index {index} is not unique in {self}")

    @property
    def space(self):
        for key in ["@id", "name"]:
            if key in self._space_spec:
                return space.Space.get_instance(self._space_spec[key])
        return space.Space(None, "Unspecified space")

    @property
    def parcellation(self):
        for key in ["@id", "name"]:
            if key in self._parcellation_spec:
                return parcellation.Parcellation.get_instance(self._parcellation_spec[key])
        logger.warn(
            f"Cannot determine parcellation of {self.__class__.__name__} "
            f"{self.name} from {self._parcellation_spec}"
        )
        return None

    @property
    def labels(self):
        """
        The set of all label indices defined in this map,
        including "None" if not defined for one or more regions.
        """
        return {d.label for v in self._indices.values() for d in v}

    @property
    def maptype(self) -> MapType:
        if all(isinstance(_, int) for _ in self.labels):
            return MapType.LABELLED
        elif self.labels == {None}:
            return MapType.CONTINUOUS
        else:
            raise RuntimeError(
                f"Inconsistent label indices encountered in {self}"
            )

    def __len__(self):
        return len(self.volumes)

    @property
    def regions(self):
        return list(self._indices)

    def fetch(
        self,
        region: str = None,
        index: MapIndex = None,
        **kwargs
    ):
        """
        Fetches one particular volume of this parcellation map.
        If there's only one volume, this is the default, otherwise further
        specication is requested:
        - the volume index,
        - the MapIndex (which results in a regional map being returned)

        You might also consider fetch_iter() to iterate the volumes, or compress()
        to produce a single-volume parcellation map.

        Parameters
        ----------
        region: str
            Specification of a region name, resulting in a regional map
            (mask or continuous map) to be returned.
        index: MapIndex
            Explicit specification of the map index, typically resulting
            in a regional map (mask or continuous map) to be returned.
            Note that supplying 'region' will result in retrieving the map index of that region
            automatically.
        """
        if not any(_ is None for _ in [region, index]):
            raise ValueError("'Region' and 'volume' cannot be specified at the same time in fetch().")

        if isinstance(region, str):
            mapindex = self.get_index(region)
        elif index is not None:
            assert isinstance(index, MapIndex)
            mapindex = index
        elif len(self.volumes) == 1:  # only 1 volume, can fetch without index/region
            mapindex = MapIndex(volume=0, label=None)
        else:
            raise ValueError(
                "Map provides multiple volumes, use 'index' or "
                "'region' to specify which one to fetch."
            )

        if "fragment" in kwargs:
            if (mapindex.fragment is not None) and (kwargs['fragment'] != mapindex.fragment):
                raise ValueError(
                    "Conflicting specifications for fetching volume fragment: "
                    f"{mapindex.fragment} / {kwargs['fragment']}"
                )
            mapindex.fragment = kwargs.pop("fragment")

        if mapindex.volume >= len(self.volumes):
            raise ValueError(
                f"{self} provides {len(self)} mapped volumes, but #{mapindex.volume} was requested."
            )

        try:
            result = self.volumes[mapindex.volume or 0].fetch(fragment=mapindex.fragment, **kwargs)
        except requests.SiibraHttpRequestError as e:
            print(str(e))
        
        if mapindex.label is not None:  # label requested, convert result map to region mask
            result = Nifti1Image(
                (result.get_fdata() == mapindex.label).astype('uint8'),
                result.affine
            )

        if result is None:
            raise RuntimeError(f"Error fetching {mapindex} from {self} as {format}.")
        return result

    @property
    def provides_image(self):
        return any(v.provides_image for v in self.volumes)

    @property
    def provides_mesh(self):
        return any(v.provides_mesh for v in self.volumes)

    @property
    def formats(self):
        return {f for v in self.volumes for f in v.formats}

    @property
    def affine(self):
        if self._affine_cached is None:
            # we compute the affine from a volumetric volume provider
            for fmt in _volume.Volume.SUPPORTED_FORMATS:
                if fmt not in _volume.Volume.MESH_FORMATS:
                    try:
                        self._affine_cached = self.fetch(0, format=fmt).affine
                        break
                    except RuntimeError:
                        continue
            else:
                raise RuntimeError(f"No volumetric provider in {self} to derive the affine matrix.")
        if not isinstance(self._affine_cached, np.ndarray):
            logger.error("invalid affine:", self._affine_cached)
        return self._affine_cached

    def fetch_iter(self, **kwargs):
        """
        Returns an iterator to fetch all mapped volumes sequentially.
        All arguments are passed on to func:`~siibra.Map.fetch`
        """
        return (
            self.fetch(MapIndex(volume=i, label=None), **kwargs)
            for i in range(len(self))
        )

    def __iter__(self):
        return self.fetch_iter()

    def compress(self, **kwargs):
        """
        Converts this map into a labelled 3D parcellation map, obtained
        by taking the voxelwise maximum across the mapped volumes, and
        re-labelling regions sequentially.
        """
        next_labelindex = 1
        region_indices = defaultdict(list)

        # initialize empty volume according to the template
        template = self.space.get_template().fetch(**kwargs)
        result_data = np.zeros_like(np.asanyarray(template.dataobj))
        voxelwise_max = np.zeros_like(result_data)
        result_nii = Nifti1Image(result_data, template.affine)
        interpolation = 'nearest' if self.maptype == MapType.LABELLED else 'linear'

        for vol in tqdm(
            range(len(self)), total=len(self), unit='maps',
            desc=f"Compressing {len(self)} {self.maptype.name.lower()} volumes into single-volume parcellation"
        ):

            img = self.fetch(vol)
            if np.linalg.norm(result_nii.affine - img.affine) > 1e-14:
                logger.debug(f"Compression requires to resample volume {vol} ({interpolation})")
                img = image.resample_to_img(img, result_nii, interpolation)
            img_data = np.asanyarray(img.dataobj)

            if self.maptype == MapType.LABELLED:
                labels = set(np.unique(img_data)) - {0}
            else:
                labels = {None}

            for label in labels:
                with QUIET:
                    region = self.get_region(label=label, volume=vol)
                if region is None:
                    logger.warn(f"Label index {label} is observed in map volume {self}, but no region is defined for it.")
                    continue
                region_indices[region.name].append({"volume": 0, "label": next_labelindex})
                if label is None:
                    update_voxels = (img_data > voxelwise_max)
                else:
                    update_voxels = (img_data == label)
                result_data[update_voxels] = next_labelindex
                voxelwise_max[update_voxels] = img_data[update_voxels]
                next_labelindex += 1

        return Map(
            identifier=f"{create_key(self.name)}_compressed",
            name=f"{self.name} compressed",
            space_spec=self._space_spec,
            parcellation_spec=self._parcellation_spec,
            indices=region_indices,
            volumes=[
                _volume.Volume(
                    space_spec=self._space_spec,
                    providers=[nifti.NiftiProvider(result_nii)]
                )
            ]
        )

    def compute_centroids(self):
        """
        Compute a dictionary of the centroids of all regions in this map.
        """
        centroids = {}
        # list of regions sorted by mapindex
        regions = sorted(self._indices.items(), key=lambda v: min(_.volume for _ in v[1]))
        current_volume = -1
        maparr = None
        for regionname, indexlist in tqdm(regions, unit="regions", desc="Computing centroids"):
            assert len(indexlist) == 1
            index = indexlist[0]
            if index.label == 0:
                continue
            if index.volume != current_volume:
                current_volume = index.volume
                with QUIET:
                    mapimg = self.fetch(index.volume)
                maparr = np.asanyarray(mapimg.dataobj)
            if index.label is None:
                # should be a continous map then
                assert self.maptype == MapType.CONTINUOUS
                centroid_vox = np.array(np.where(maparr > 0)).mean(1)
            else:
                centroid_vox = np.array(np.where(maparr == index.label)).mean(1)
            assert regionname not in centroids
            centroids[regionname] = point.Point(
                np.dot(mapimg.affine, np.r_[centroid_vox, 1])[:3], space=self.space
            )
        return centroids

    def colorize(self, values: dict):
        """Colorize the map with the provided regional values.

        Parameters
        ----------
        values : dict
            Dictionary mapping regions to values

        Return
        ------
        Nifti1Image
        """

        result = None
        for volidx, vol in enumerate(self.fetch_iter()):
            if isinstance(vol, dict):
                raise NotImplementedError(f"Map colorization not yet implemented for meshes.")
            img = np.asanyarray(vol.dataobj)
            maxarr = np.zeros_like(img)
            for r, value in values.items():
                index = self.get_index(r)
                if index.volume != volidx:
                    continue
                if result is None:
                    result = np.zeros_like(img)
                    affine = vol.affine
                if index.label is None:
                    updates = img > maxarr
                    result[updates] = value
                    maxarr[updates] = img[updates]
                else:
                    result[img == index.label] = value

        return Nifti1Image(result, affine)

    def get_colormap(self):
        """Generate a matplotlib colormap from known rgb values of label indices."""
        from matplotlib.colors import ListedColormap
        import numpy as np

        colors = {}
        for regionname, indices in self._indices.items():
            for index in indices:
                if index.label is None:
                    continue
                region = self.get_region(index=index)
                if region.rgb is not None:
                    colors[index.label] = region.rgb

        pallette = np.array(
            [
                list(colors[i]) + [1] if i in colors else [0, 0, 0, 0]
                for i in range(max(colors.keys()) + 1)
            ]
        ) / [255, 255, 255, 1]
        return ListedColormap(pallette)

    def sample_locations(self, regionspec, numpoints: int):
        """ Sample 3D locations inside a given region.

        The probability distribution is approximated from the region mask
        based on the squared distance transform.

        regionspec: valid region specification
            Region to be used
        numpoints: int
            Number of samples to draw

        Return
        ------
        samples : PointSet in physcial coordinates corresponding to this parcellationmap.

        """
        index = self.get_index(regionspec)
        mask = self.fetch(index=index)
        arr = np.asanyarray(mask.dataobj)
        if arr.dtype.char in np.typecodes['AllInteger']:
            # a binary mask - use distance transform to get sampling weights
            W = distance_transform_edt(np.asanyarray(mask.dataobj))**2
        else:
            # a continuous map - interpret directly as weights
            W = arr
        p = (W / W.sum()).ravel()
        XYZ_ = np.array(
            np.unravel_index(np.random.choice(len(p), numpoints, p=p), W.shape)
        ).T
        XYZ = np.dot(mask.affine, np.c_[XYZ_, np.ones(numpoints)].T)[:3, :].T
        return pointset.PointSet(XYZ, space=self.space)

    def assign(
        self,
        item: Union[point.Point, pointset.PointSet, Nifti1Image],
        minsize_voxel=1,
        lower_threshold=0.0,
    ):
        """Assign an input image to brain regions.

        The input image is assumed to be defined in the same coordinate space
        as this parcellation map.

        Parameters
        ----------
        item: Point, PointSet, or Nifti1Image
            A spatial object defined in the same physical reference space as this
            parcellation map, which could be a point, set of points, or image.
            If it is an image, it will be resampled to the same voxel space if its affine
            transforation differs from that of the parcellation map.
            Resampling will use linear interpolation for float image types,
            otherwise nearest neighbor.
        minsize_voxel: int, default: 1
            Minimum voxel size of image components to be taken into account.
        lower_threshold: float, default: 0
            Lower threshold on values in the continuous map. Values smaller than
            this threshold will be excluded from the assignment computation.

        Return
        ------
        assignments : pandas Dataframe
            A table of associated regions and their scores per component found in the input image,
            or per coordinate provived.
            The scores are:
                - Value: Maximum value of the voxels in the map covered by an input coordinate or
                  input image signal component.
                - Pearson correlation coefficient between the brain region map and an input image signal
                  component (NaN for exact coordinates)
                - "Contains": Percentage of the brain region map contained in an input image signal component,
                  measured from their binarized masks as the ratio between the volume of their interesection
                  and the volume of the brain region (NaN for exact coordinates)
                - "Contained"": Percentage of an input image signal component contained in the brain region map,
                  measured from their binary masks as the ratio between the volume of their interesection
                  and the volume of the input image signal component (NaN for exact coordinates)
        components: Nifti1Image, or None
            If the input was an image, this is a labelled volume mapping the detected components
            in the input image, where pixel values correspond to the "component" column of the
            assignment table. If the input was a Point or PointSet, this is None.
        """

        components = None
        if isinstance(item, point.Point):
            assignments = self._assign_points(pointset.PointSet([item], item.space, sigma_mm=item.sigma), lower_threshold)
        elif isinstance(item, pointset.PointSet):
            assignments = self._assign_points(item, lower_threshold)
        elif isinstance(item, Nifti1Image):
            assignments = self._assign_image(item, minsize_voxel, lower_threshold)
        else:
            raise RuntimeError(
                f"Items of type {item.__class__.__name__} cannot be used for region assignment."
            )

        # format assignments as pandas dataframe
        if len(assignments) == 0:
            df = pd.DataFrame(
                columns=["Structure", "Volume", "Region", "Value", "Correlation", "IoU", "Contains", "Contained"]
            )
        else:
            result = np.array(assignments)
            ind = np.lexsort((-result[:, -1], result[:, 0]))
            region_lut = {
                (mi.volume, mi.label): r
                for r, l in self._indices.items()
                for mi in l
            }
            if self.maptype == MapType.CONTINUOUS:
                regions = [region_lut[v, None] for v in result[ind, 1].astype('int')]
            else:
                regions = [region_lut[v, l] for v, l in result[ind, 1:3].astype('int')]
            df = pd.DataFrame(
                {
                    "Structure": result[ind, 0].astype("int"),
                    "Volume": result[ind, 1].astype("int"),
                    "Region": regions,
                    "Value": result[ind, 2],
                    "Correlation": result[ind, 6],
                    "IoU": result[ind, 3],
                    "Contains": result[ind, 5],
                    "Contained": result[ind, 4],
                }
            ).dropna(axis=1, how="all")

        if components is None:
            return df
        else:
            return df

    @staticmethod
    def iterate_connected_components(img: Nifti1Image):
        """
        Provide an iterator over masks of connected components in the given image.
        """
        from skimage import measure
        imgdata = np.asanyarray(img.dataobj).squeeze()
        components = measure.label(imgdata > 0)
        component_labels = np.unique(components)
        assert component_labels[0] == 0
        return (
            (label, Nifti1Image((components == label).astype('uint8'), img.affine))
            for label in component_labels[1:]
        )

    def _read_voxel(
        self,
        x: Union[int, np.ndarray, List],
        y: Union[int, np.ndarray, List],
        z: Union[int, np.ndarray, List]
    ):
        if isinstance(x, int):
            return [
                (None, volume, np.asanyarray(volimg.dataobj)[x, y, z])
                for volume, volimg in enumerate(self)
            ]
        else:
            return [
                (pointindex, volume, value)
                for volume, volimg in enumerate(self)
                for pointindex, value
                in enumerate(np.asanyarray(volimg.dataobj)[x, y, z])
            ]

    def _assign_points(self, points, lower_threshold: float):
        """
        assign a PointSet to this parcellation map.

        Parameters:
        -----------
        lower_threshold: float, default: 0
            Lower threshold on values in the continuous map. Values smaller than
            this threshold will be excluded from the assignment computation.
        """
        assignments = []

        if points.space != self.space:
            logger.info(
                f"Coordinates will be converted from {points.space.name} "
                f"to {self.space.name} space for assignment."
            )
        # convert sigma to voxel coordinates
        scaling = np.array(
            [np.linalg.norm(self.affine[:, i]) for i in range(3)]
        ).mean()
        phys2vox = np.linalg.inv(self.affine)

        # if all points have the same sigma, and lead to a standard deviation
        # below 3 voxels, we are much faster with a multi-coordinate readout.
        if points.has_constant_sigma:
            sigma_vox = points.sigma[0] / scaling
            if sigma_vox < 3:
                logger.info("Points have constant single-voxel precision, using direct multi-point lookup.")
                X, Y, Z = (np.dot(phys2vox, points.warp(self.space.id).homogeneous.T) + 0.5).astype("int")[:3]
                for pointindex, vol, value in self._read_voxel(X, Y, Z):
                    if value > lower_threshold:
                        assignments.append(
                            [pointindex, vol, value, np.nan, np.nan, np.nan, np.nan]
                        )
            return assignments

        # if we get here, we need to handle each point independently.
        # This is much slower but more precise in dealing with the uncertainties
        # of the coordinates.
        for pointindex, pt in tqdm(
            enumerate(points.warp(self.space.id)),
            total=len(points), desc="Warping points",
        ):
            sigma_vox = pt.sigma / scaling
            if sigma_vox < 3:
                # voxel-precise - just read out the value in the maps
                N = len(self)
                logger.debug(f"Assigning coordinate {tuple(pt)} to {N} maps")
                x, y, z = (np.dot(phys2vox, pt.homogeneous) + 0.5).astype("int")[:3]
                vals = self._read_voxel(x, y, z)
                for _, vol, value in vals:
                    if value > lower_threshold:
                        assignments.append(
                            [pointindex, vol, value, np.nan, np.nan, np.nan, np.nan]
                        )
            else:
                logger.debug(
                    f"Assigning uncertain coordinate {tuple(pt)} to {len(self)} maps."
                )
                kernel = create_gaussian_kernel(sigma_vox, 3)
                r = int(kernel.shape[0] / 2)  # effective radius
                xyz_vox = (np.dot(phys2vox, pt.homogeneous) + 0.5).astype("int")
                shift = np.identity(4)
                shift[:3, -1] = xyz_vox[:3] - r
                # build niftiimage with the Gaussian blob,
                # then recurse into this method with the image input
                W = Nifti1Image(dataobj=kernel, affine=np.dot(self.affine, shift))
                T, _ = self.assign(W, lower_threshold=lower_threshold)
                assignments.extend(
                    [
                        [pointindex, volume, value, iou, contained, contains, rho]
                        for (_, volume, _, value, rho, iou, contains, contained) in T.values
                    ]
                )
        return assignments

    def _assign_image(self, queryimg: Nifti1Image, minsize_voxel: int, lower_threshold: float):
        """
        Assign an image volume to this parcellation map.

        Parameters:
        -----------
        minsize_voxel: int, default: 1
            Minimum voxel size of image components to be taken into account.
        lower_threshold: float, default: 0
            Lower threshold on values in the continuous map. Values smaller than
            this threshold will be excluded from the assignment computation.
        """
        assignments = []

        # resample query image into this image's voxel space, if required
        if (queryimg.affine - self.affine).sum() == 0:
            queryimg = queryimg
        else:
            if issubclass(np.asanyarray(queryimg.dataobj).dtype.type, np.integer):
                interp = "nearest"
            else:
                interp = "linear"
            queryimg = image.resample_img(
                queryimg,
                target_affine=self.affine,
                target_shape=self.shape,
                interpolation=interp,
            )

        with QUIET:
            for mode, maskimg in Map.iterate_connected_components(queryimg):
                for vol, vol_img in enumerate(self):
                    vol_data = np.asanyarray(vol_img.dataobj)
                    labels = [v.label for L in self._indices.values() for v in L if v.volume == vol]
                    for label in tqdm(labels):
                        targetimg = Nifti1Image((vol_data == label).astype('uint8'), vol_img.affine)
                        scores = compare_maps(maskimg, targetimg)
                        if scores["overlap"] > 0:
                            assignments.append(
                                [mode, vol, label, scores["iou"], scores["contained"], scores["contains"], scores["correlation"]]
                            )

        return assignments
