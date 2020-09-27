from abc import ABC,abstractmethod

class Feature(ABC):
    """ 
    Abstract base class for all data features.
    """

    @abstractmethod
    def matches(self,atlas):
        """
        Returns True if this feature should be considered part of the current
        selection of the atlas object, otherwise else.
        """
        pass

    @abstractmethod
    def __str__(self):
        """
        Print a reasonable name of this feature.
        """
        return 

class SpatialFeature(Feature):
    """
    Base class for coordinate-anchored data features.
    """

    def __init__(self,space,location):
        self.space = space
        self.location = location
 
    def matches(self,atlas):
        """
        Returns true if the location of this feature is inside the selected
        region of the atlas, according to the mask in the reference space.
        """
        return atlas.coordinate_selected(self.space,self.location)

    def __str__(self):
        return "Features in '{space}' at {loc[0]}/{loc[1]}/{loc[2]}".format(
                space=self.space, loc=self.location)

class RegionalFeature(Feature):
    """
    Base class for region-anchored data features (semantic anchoring to region
    names instead of coordinates).
    TODO store region as an object that has a link to the parcellation
    """

    def __init__(self,region):
        self.region = region
 
    def matches(self,atlas):
        """
        Returns true if this feature is linked to the currently selected region
        in the atlas.
        """
        matching_regions = atlas.selected_region.find(self.region,exact=False)
        for region in matching_regions:
            if atlas.region_selected(region):
                return True

class GlobalFeature(Feature):
    """
    Base class for data features which apply to the atlas as a whole
    instead of a particular location or region. A typical example is a
    connectivity matrix, which applies to all regions in the atlas.
    """

    def __init__(self,parcellation):
        self.parcellation = parcellation
 
    def matches(self,atlas):
        """
        Returns true if this global feature is related to the given atlas.
        """
        if self.parcellation == atlas.selected_parcellation:
            return True

