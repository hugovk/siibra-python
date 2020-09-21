import io
import logging

import requests
import pandas as pd
import PIL.Image as Image
from os import path

from brainscapes import kg_service


class ReceptorData:
    # data rows
    profiles = {}
    # images
    autoradiographs = {}
    # math symbols for receptors

    def __init__(self, kg_response):

        self.regions = [e['https://schema.hbp.eu/myQuery/name']
                        for e in kg_response['https://schema.hbp.eu/myQuery/parcellationRegion']]

        for fname in kg_response['https://schema.hbp.eu/myQuery/v1.0.0']:

            if 'receptors.tsv' in fname:
                bytestream = io.BytesIO(requests.get(fname).content)
                self.__symbols = pd.read_csv(bytestream,sep='\t')
                self.receptor_label = {r._1:r._2
                                       for r in self.__symbols.itertuples()}

            # Receive cortical profiles, if any
            if '_pr_' in fname:
                suffix = path.splitext(fname)[-1]
                if suffix == '.tsv':
                    receptor_type,basename = fname.split("/")[-2:]
                    if receptor_type in basename:
                        bytestream = io.BytesIO(requests.get(fname).content)
                        self.profiles[receptor_type] = pd.read_csv(bytestream,sep='\t')
                else:
                    logging.debug('Expected .tsv for profile, got {}: {}'.format(suffix,fname))

            if '_ar_' in fname:
                receptor_type,basename = fname.split("/")[-2:]
                if receptor_type in basename:
                    bytestream = requests.get(fname).content
                    self.autoradiographs[receptor_type] = Image.open(io.BytesIO(bytestream))

            if '_fp_' in fname:
                bytestream = io.BytesIO(requests.get(fname).content)
                self.fingerprint = pd.read_csv(bytestream,sep='\t')


if __name__ == '__main__':
    kg_response = kg_service.execute_query_by_id('minds', 'core', 'dataset', 'v1.0.0', 'bs_datasets_tmp')
    print(kg_response)
    receptor_data = ReceptorData(kg_response)
