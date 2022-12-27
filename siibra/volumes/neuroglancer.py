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

from . import volume

from ..commons import logger, MapType, MapIndex
from ..retrieval import requests, cache
from ..locations import boundingbox

from neuroglancer_scripts.precomputed_io import get_IO_for_existing_dataset
from neuroglancer_scripts.accessor import get_accessor_for_url
from neuroglancer_scripts.mesh import read_precomputed_mesh, affine_transform_mesh
from io import BytesIO
import nibabel as nib
import os
import numpy as np


class NeuroglancerVolume(volume.VolumeProvider, srctype="neuroglancer/precomputed"):
    # Number of bytes at which an image array is considered to large to fetch
    MAX_GiB = 0.2

    # Wether to keep fetched data in local cache
    USE_CACHE = False

    @property
    def MAX_BYTES(self):
        return self.MAX_GiB * 1024 ** 3

    def __init__(self, url: str, transform_nm: np.ndarray = None):
        volume.VolumeProvider.__init__(self)
        self.url = url
        self._scales_cached = None
        self._io = None
        self._transform_nm = transform_nm

    @property
    def transform_nm(self):
        if self._transform_nm is not None:
            return self._transform_nm
        try:
            res = requests.HttpRequest(f"{self.url}/transform.json").get()
        except requests.SiibraHttpRequestError:
            res = None
        if res is not None:
            self._transform_nm = np.array(res)
            return self._transform_nm

        self._transform_nm = np.identity(1)
        logger.warn(f"No transform.json found at {self.url}, using identity.")
        return self._transform_nm

    @transform_nm.setter
    def transform_nm(self, val):
        self._transform_nm = val

    @property
    def map_type(self):
        if self._io is None:
            self._bootstrap()
        return (
            MapType.LABELLED
            if self._io.info.get("type") == "segmentation"
            else MapType.CONTINUOUS
        )

    @map_type.setter
    def map_type(self, val):
        if val is not None:
            logger.debug(
                "NeuroglancerVolume can determine its own maptype from self._io.info.get('type')"
            )

    def _bootstrap(self):
        accessor = get_accessor_for_url(self.url)
        self._io = get_IO_for_existing_dataset(accessor)
        self._scales_cached = sorted(
            [NeuroglancerScale(self, i) for i in self._io.info["scales"]]
        )

    @property
    def dtype(self):
        if self._io is None:
            self._bootstrap()
        return np.dtype(self._io.info["data_type"])

    @property
    def scales(self):
        if self._scales_cached is None:
            self._bootstrap()
        return self._scales_cached

    def fetch(self, resolution_mm: float = None, voi: boundingbox.BoundingBox = None, **kwargs):
        # the caller has to make sure voi is defined in the correct reference space
        scale = self._select_scale(resolution_mm=resolution_mm, bbox=voi)
        return scale.fetch(voi)

    def get_shape(self, resolution_mm=None):
        scale = self._select_scale(resolution_mm)
        return scale.size

    def is_float(self):
        return self.dtype.kind == "f"

    def _select_scale(self, resolution_mm: float, bbox: boundingbox.BoundingBox = None):
        if resolution_mm is None:
            suitable = self.scales
        elif resolution_mm < 0:
            suitable = [self.scales[0]]
        else:
            suitable = sorted(s for s in self.scales if s.resolves(resolution_mm))

        if len(suitable) > 0:
            scale = suitable[-1]
        else:
            scale = self.scales[0]
            logger.warn(
                f"Requested resolution {resolution_mm} is not available. "
                f"Falling back to the highest possible resolution of "
                f"{', '.join(map('{:.2f}'.format, scale.res_mm))} mm."
            )

        scale_changed = False
        while scale._estimate_nbytes(bbox) > self.MAX_BYTES:
            scale = scale.next()
            scale_changed = True
            if scale is None:
                raise RuntimeError(
                    f"Fetching bounding box {bbox} is infeasible "
                    f"relative to the limit of {self.MAX_BYTES/1024**3}GiB."
                )
        if scale_changed:
            logger.warn(f"Resolution was reduced to {scale.res_mm} to provide a feasible volume size")
        return scale


class NeuroglancerScale:
    """One scale of a NeuroglancerVolume."""

    color_warning_issued = False

    def __init__(self, volume: NeuroglancerVolume, scaleinfo: dict):
        self.volume = volume
        self.chunk_sizes = np.array(scaleinfo["chunk_sizes"]).squeeze()
        self.encoding = scaleinfo["encoding"]
        self.key = scaleinfo["key"]
        self.res_nm = np.array(scaleinfo["resolution"]).squeeze()
        self.size = scaleinfo["size"]
        self.voxel_offset = np.array(scaleinfo["voxel_offset"])

    @property
    def res_mm(self):
        return self.res_nm / 1e6

    def resolves(self, resolution_mm):
        """Test wether the resolution of this scale is sufficient to provide the given resolution."""
        return any(r / 1e6 <= resolution_mm for r in self.res_nm)

    def __lt__(self, other):
        """Sort scales by resolution."""
        return all(self.res_nm[i] < other.res_nm[i] for i in range(3))

    def __repr__(self):
        return str(self)

    def __str__(self):
        return f"{self.__class__.__name__} {self.key}"

    def _estimate_nbytes(self, bbox: boundingbox.BoundingBox = None):
        """Estimate the size image array to be fetched in bytes, given a bounding box."""
        if bbox is None:
            bbox_ = boundingbox.BoundingBox((0, 0, 0), self.size, space=None)
        else:
            bbox_ = bbox.transform(np.linalg.inv(self.affine))
        result = self.volume.dtype.itemsize * bbox_.volume
        logger.debug(
            f"Approximate size for fetching resolution "
            f"({', '.join(map('{:.2f}'.format, self.res_mm))}) mm "
            f"is {result/1024**3:.2f} GiB."
        )
        return result

    def next(self):
        """Returns the next scale in this volume, of None if this is the last."""
        my_index = self.volume.scales.index(self)
        if my_index < len(self.volume.scales):
            return self.volume.scales[my_index + 1]
        else:
            return None

    def prev(self):
        """Returns the previous scale in this volume, or None if this is the first."""
        my_index = self.volume.scales.index(self)
        print(f"Index of {self.key} is {my_index} of {len(self.volume.scales)}.")
        if my_index > 0:
            return self.volume.scales[my_index - 1]
        else:
            return None

    @property
    def affine(self):
        scaling = np.diag(np.r_[self.res_nm, 1.0])
        affine = np.dot(self.volume.transform_nm, scaling)
        affine[:3, :] /= 1e6
        return affine

    def _point_to_lower_chunk_idx(self, xyz):
        return (
            np.floor((np.array(xyz) - self.voxel_offset) / self.chunk_sizes)
            .astype("int")
            .ravel()
        )

    def _point_to_upper_chunk_idx(self, xyz):
        return (
            np.ceil((np.array(xyz) - self.voxel_offset) / self.chunk_sizes)
            .astype("int")
            .ravel()
        )

    def _read_chunk(self, gx, gy, gz):
        if self.volume.USE_CACHE:
            cachefile = cache.CACHE.build_filename(
                "{}_{}_{}_{}_{}".format(self.volume.url, self.key, gx, gy, gz),
                suffix=".npy",
            )
            if os.path.isfile(cachefile):
                return np.load(cachefile)

        x0 = gx * self.chunk_sizes[0]
        y0 = gy * self.chunk_sizes[1]
        z0 = gz * self.chunk_sizes[2]
        x1, y1, z1 = np.minimum(self.chunk_sizes + [x0, y0, z0], self.size)
        chunk_czyx = self.volume._io.read_chunk(self.key, (x0, x1, y0, y1, z0, z1))
        if not chunk_czyx.shape[0] == 1 and not self.color_warning_issued:
            logger.warn(
                "Color channel data is not yet supported. Returning first channel only."
            )
            self.color_warning_issued = True
        chunk_zyx = chunk_czyx[0]

        if self.volume.USE_CACHE:
            np.save(cachefile, chunk_zyx)
        return chunk_zyx

    def fetch(self, voi: boundingbox.BoundingBox = None, **kwargs):

        # define the bounding box in this scale's voxel space
        if voi is None:
            bbox_ = boundingbox.BoundingBox((0, 0, 0), self.size, space=None)
        else:
            bbox_ = voi.transform(np.linalg.inv(self.affine))

        for dim in range(3):
            if bbox_.shape[dim] < 1:
                logger.warn(
                    f"Bounding box in voxel space will be enlarged to voxel size 1 along axis {dim}."
                )
                bbox_.maxpoint[dim] = bbox_.maxpoint[dim] + 1

        # extract minimum and maximum the chunk indices to be loaded
        gx0, gy0, gz0 = self._point_to_lower_chunk_idx(tuple(bbox_.minpoint))
        gx1, gy1, gz1 = self._point_to_upper_chunk_idx(tuple(bbox_.maxpoint))

        # create requested data volume, and fill it with the required chunk data
        shape_zyx = np.array([gz1 - gz0, gy1 - gy0, gx1 - gx0]) * self.chunk_sizes[::-1]
        data_zyx = np.zeros(shape_zyx, dtype=self.volume.dtype)
        for gx in range(gx0, gx1):
            x0 = (gx - gx0) * self.chunk_sizes[0]
            for gy in range(gy0, gy1):
                y0 = (gy - gy0) * self.chunk_sizes[1]
                for gz in range(gz0, gz1):
                    z0 = (gz - gz0) * self.chunk_sizes[2]
                    chunk = self._read_chunk(gx, gy, gz)
                    z1, y1, x1 = np.array([z0, y0, x0]) + chunk.shape
                    data_zyx[z0:z1, y0:y1, x0:x1] = chunk

        # determine the remaining offset from the "chunk mosaic" to the
        # exact bounding box requested, to cut off undesired borders
        data_min = np.array([gx0, gy0, gz0]) * self.chunk_sizes
        x0, y0, z0 = (np.array(tuple(bbox_.minpoint)) - data_min).astype("int")
        xd, yd, zd = np.array(bbox_.shape).astype("int")
        offset = tuple(bbox_.minpoint)

        # build the nifti image
        trans = np.identity(4)[[2, 1, 0, 3], :]  # zyx -> xyz
        shift = np.c_[np.identity(4)[:, :3], np.r_[offset, 1]]
        return nib.Nifti1Image(
            data_zyx[z0: z0 + zd, y0: y0 + yd, x0: x0 + xd],
            np.dot(self.affine, np.dot(shift, trans)),
        )


class NeuroglancerMesh(volume.VolumeProvider, srctype="neuroglancer/precompmesh"):
    """
    A surface mesh provided as neuroglancer precomputed mesh.
    """
    def __init__(self, url, volume=None):
        self.volume = volume
        self.url = url
        self._info_request = requests.HttpRequest(url=self.url + "/info", func=requests.DECODERS['.json'])
        self._transform_nm = np.array(requests.HttpRequest(f"{self.url}/transform.json").data)

    @property
    def info(self):
        # HttpRequest already implements lazy loading & caching for us
        return self._info_request.data

    def _get_fragment_urls(self, meshindex: int) -> dict:
        # extract available fragment urls with their names for the given mesh index
        mesh_key = self.info.get('mesh')
        meshurl = f"{self.url}/{mesh_key}/{str(meshindex)}:0"
        meshinfo = requests.HttpRequest(url=meshurl, func=requests.DECODERS['.json']).data
        fragment_urls = {
            fragment_name: f"{self.url}/{mesh_key}/{fragment_name}"
            for fragment_name in meshinfo.get('fragments')
        }
        if len(fragment_urls) == 0:
            raise RuntimeError("No fragments found at {meshurl}")
        return fragment_urls

    def _fetch_fragment(self, url: str):
        r = requests.HttpRequest(url, func=lambda b: BytesIO(b))
        (vertices_vox, triangles_vox) = read_precomputed_mesh(r.data)
        vertices, triangles = affine_transform_mesh(vertices_vox, triangles_vox, self._transform_nm)
        vertices /= 1e6
        return vertices, triangles

    def fetch(self, fragment: str = None, **kwargs):
        """
        Fetches a particular mesh. Each mesh is a dictionary with the keys:

        - verts: an Nx3 array of coordinates (in nanometer)
        - faces: an MX3 array containing connection data of vertices
        """

        # extract the label index to identify the mesh
        if "mapindex" in kwargs:
            meshindex = kwargs.pop("mapindex").label
            if meshindex is None:
                raise ValueError(
                    f"{self.__class___.__name__} requires label to be set in 'mapindex' for fetch()."
                )
        else:
            logger.info(
                f"'mapindex' not specified when fetching from {self.__class__}. "
                "Trying to fetch a mesh labelled 1."
            )
            meshindex = 1

        for k, v in kwargs:
            logger.warn(f"{self.__class__.__name__}.fetch() ignores '{k} argument (={v})")

        fragment_urls = self._get_fragment_urls(meshindex)

        if (fragment is None) or (fragment.lower() == "all"):
            # no fragment specified, return merged fragment meshes
            if len(fragment_urls) == 1:
                verts, faces = self._fetch_fragment(next(iter(fragment_urls.values())))
            else:
                logger.info(
                    f"Fragments [{', '.join(fragment_urls.keys())}] are merged during fetch(). "
                    "You can select one of them using the 'fragment' parameter."
                )
                fragment_data = [self._fetch_fragment(u) for u in fragment_urls.values()]
                verts = np.concatenate((fragment_data[0][0], fragment_data[1][0]))
                faces = np.concatenate((fragment_data[0][1], fragment_data[1][1] + len(fragment_data[0][0])))
            return dict(zip(['verts', 'faces'], [verts, faces]))
        else:
            # match fragment to available fragments
            matched_urls = [
                url for name, url in fragment_urls.items()
                if fragment.lower() in name
            ]
            if len(matched_urls) == 1:
                verts, faces = self._fetch_fragment(matched_urls[0])
                return dict(zip(['verts', 'faces'], [verts, faces]))
            else:
                raise ValueError(
                    f"The requested mesh fragment name '{fragment}' could not be resolved at {meshurl}. "
                    f"Valid names are: {', '.join(fragment_urls.keys())}"
                )


class NeuroglancerSurfaceMesh(NeuroglancerMesh, srctype="neuroglancer/precompmesh/surface"):
    """
    Only shadows NeuroglancerMesh for the special surface srctype,
    which provides a mesh urls plus a label index for identifying the surface.
    Behaves like NeuroglancerMesh otherwise.

    TODO this class might be replaced by implementing a default label index for the parent class.
    """
    def __init__(self, spec: str, **kwargs):
        # we expect a string of the form "<url> <labelindex>",
        # and use this to set the url and label index in the parent class.
        assert ' ' in spec
        url, labelindex_, *args = spec.split(' ')
        assert labelindex_.isnumeric()
        if 'mapindex' in kwargs:
            self._mapindex = kwargs['mapindex']
            self._mapindex.label = int(labelindex_)
        else:
            self._mapindex = MapIndex(volume=None, label=int(labelindex_))
        NeuroglancerMesh.__init__(self, url=url, **kwargs)

    def fetch(self, **kwargs):
        return NeuroglancerMesh.fetch(self, mapindex=self._mapindex, **kwargs)
