# Copyright 2018-2020 Institute of Neuroscience and Medicine (INM-1), Forschungszentrum Jülich GmbH

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
from enum import Enum
import numpy as np
from abc import ABC, abstractmethod

class Glossary:
    """
    A very simple class that provides enum-like simple autocompletion for an
    arbitrary list of names.
    """
    def __init__(self,words):
        self.words = list(words)

    def __dir__(self):
        return self.words

    def __str__(self):
        return "\n".join(self.words)

    def __iter__(self):
        return (w for w in self.words)

    def __contains__(self,index):
        return index in self.__dir__()

    def __getattr__(self,name):
        if name in self.words:
            return name
        else:
            raise AttributeError("No such term: {}".format(name))

def create_key(name):
    """
    Creates an uppercase identifier string that includes only alphanumeric
    characters and underscore from a natural language name.
    """
    return re.sub(
            r' +','_',
            "".join([e if e.isalnum() else " " 
                for e in name]).upper().strip() 
            )

class MapType(Enum):
    LABELLED = 1
    CONTINUOUS = 2

class ImageProvider(ABC):

    @abstractmethod
    def fetch(self,resolution_mm=None, voi=None):
        """
        Provide access to image data.
        """
        pass


def bbox3d(A):
    """
    Bounding box of nonzero values in a 3D array.
    https://stackoverflow.com/questions/31400769/bounding-box-of-numpy-array
    """
    r = np.any(A, axis=(1, 2))
    c = np.any(A, axis=(0, 2))
    z = np.any(A, axis=(0, 1))
    rmin, rmax = np.where(r)[0][[0, -1]]
    cmin, cmax = np.where(c)[0][[0, -1]]
    zmin, zmax = np.where(z)[0][[0, -1]]
    return np.array([
        [rmin, rmax], 
        [cmin, cmax], 
        [zmin, zmax],
        [1,1]
    ])