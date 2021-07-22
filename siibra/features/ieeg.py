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

import json
from siibra.features.ebrainsquery import EbrainsRegionalFeatureExtractor
import numpy as np
from gitlab import Gitlab
import os
import re
from tqdm import tqdm

from .. import logger,spaces,parcellations
from ..commons import ParcellationIndex
from ..region import Region
from .feature import RegionalFeature,SpatialFeature
from .extractor import FeatureExtractor

class IEEG_Electrode:
    def __init__(self,id,kg_id,subject_id,space):
        self.electrode_id = id
        self.subject_id = subject_id
        self.kg_id = kg_id
        self.space = space
        self.contact_points = {}

    def __str__(self):
        return f"Electrode: subject:{self.subject_id} id:{self.electrode_id} dataset:{self.kg_id}"

    def __repr__(self):
        return self.__str__()
    
    def register_contact_point(self,contactpoint):
        if contactpoint.id in self.contact_points:
           raise ValueError(f"Contact point with id {contactpoint.id} already registered to {self}") 
        self.contact_points[contactpoint.id] = contactpoint

    def contactpoints_available(self):
        return self.contact
        

class IEEG_ContactPoint(SpatialFeature):
    """
    Basic regional feature for iEEG contact points.
    """
    def __init__(self, electrode, id, coord ):
        #RegionalFeature.__init__(self,region)
        SpatialFeature.__init__(self,electrode.space,coord)
        self.electrode = electrode
        self.id = id
        electrode.register_contact_point(self)

    def next(self):
        """
        Returns the next contact point of the same electrode, if any.
        """
        ids_available = list(self.electrode.contact_points.keys())
        my_index = ids_available.index(self.id)
        next_index = my_index+1
        if next_index<len(ids_available):
            next_id = ids_available[next_index]
            return self.electrode.contact_points[next_id]
        else:
            return None

    def prev(self):
        """
        Returns the previous contact point of the same electrode, if any.
        """
        ids_available = list(self.electrode.contact_points.keys())
        my_index = ids_available.index(self.id)
        prev_index = my_index-1
        if prev_index>=0:
            prev_id = ids_available[prev_index]
            return self.electrode.contact_points[prev_id]
        else:
            return None

def load_ptsfile(data):
    sub_id = os.path.basename(data.file_name).split('_')[0]
    result = {'subject_id':sub_id}
    lines = data.decode().decode('utf-8').split("\n")
    N = int(lines[2].strip())
    result['electrodes'] = {}
    for i in range(N):
        fields = re.split('\t',lines[i+3].strip())
        electrode_id,contact_point_id = re.split('(\d+)',fields[0])[:-1]
        if electrode_id not in result['electrodes']:
            result['electrodes'][electrode_id] = {}
        assert(contact_point_id not in result['electrodes'][electrode_id])
        result['electrodes'][electrode_id][contact_point_id] = list(map(float,fields[1:4]))
    return result


class IEEG_ContactPointExtractor(FeatureExtractor):

    _FEATURETYPE = IEEG_ContactPoint
    __files = None

    def __init__(self,atlas):

        FeatureExtractor.__init__(self,atlas)
        self.load_contactpoints()

    def __load_files(self,subfolder,suffix):
        project = Gitlab('https://jugit.fz-juelich.de').projects.get(3009)
        files = [f['name'] 
                for f in project.repository_tree(path=subfolder,ref='master',all=True)
                if f['type']=='blob' 
                and f['name'].endswith(suffix)]
        self.__class__.__files=[]
        for fname in files:
            f = project.files.get(file_path=os.path.join(subfolder,fname), ref='master')
            data = load_ptsfile(f)
            self.__class__.__files.append({
                'data':data,
                'fname': fname})
        
    def load_contactpoints(self):
        """
        Load contact point list and create features.
        """
        if self.__class__.__files is None:
            self.__load_files('ieeg_contact_points','pts')
        electrodes = {}
        for obj in self.__class__.__files: 
            subject_id=obj['data']['subject_id']
            if subject_id not in electrodes:
                electrodes[subject_id] = {}
            for electrode_id,contact_points in obj['data']['electrodes'].items():
                if electrode_id not in electrodes[subject_id]:
                    electrodes[subject_id][electrode_id] = IEEG_Electrode(
                        id=electrode_id,
                        kg_id="ca952092-3013-4151-abcc-99a156fe7c83",
                        subject_id=subject_id, 
                        space=spaces["mni152"])
                electrode = electrodes[subject_id][electrode_id]
                for contact_point_id,coord in contact_points.items():
                    self.register( IEEG_ContactPoint(
                        electrode=electrode,id=contact_point_id,coord=coord ))

if __name__ == '__main__':
    extractor = IEEG_ContactPointExtractor()

