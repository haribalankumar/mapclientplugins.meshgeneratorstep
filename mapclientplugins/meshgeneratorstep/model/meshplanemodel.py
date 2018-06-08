import imghdr
import os
import re
import get_image_size
import numpy as np

from opencmiss.utils.maths import vectorops
from opencmiss.utils.zinc import createFiniteElementField, createSquare2DFiniteElement, \
    createMaterialUsingImageField, createVolumeImageField
from opencmiss.zinc.field import Field

from mapclientplugins.meshgeneratorstep.model.meshalignmentmodel import MeshAlignmentModel
from mapclientplugins.meshgeneratorstep.model.fixcoordinatesmixin import FixCoordinatesMixin


class MeshPlaneModel(MeshAlignmentModel, FixCoordinatesMixin):

    def __init__(self, region):
        super(MeshPlaneModel, self).__init__()
        self._region_name = "plane_mesh"
        self._timekeeper = None
        self._frame_count = 0
        self._parent_region = region
        self._region = None
        self._settings = {
            'display-image-plane': True,
            'image-plane-fixed': False,
            'alignment': {},
        }

    def getPlaneInfo(self):
        node_locations = self.getNodeLocations()
        u1 = vectorops.sub(node_locations[1], node_locations[0])
        u2 = vectorops.sub(node_locations[2], node_locations[0])

        plane_centre = np.mean(node_locations, axis=0).tolist()
        c = vectorops.cross(u1, u2)
        normal = vectorops.normalize(c)
        up = vectorops.normalize(u2)
        return normal, up, plane_centre

    def setImageInfo(self, image_info):
        images = []
        if image_info is not None:
            location = image_info.location()
            if os.path.isdir(location):
                for item in sorted(os.listdir(location), key=alphanum_key):
                    image_candidate = os.path.join(location, item)
                    if imghdr.what(image_candidate):
                        images.append(image_candidate)
            elif os.path.exists(location):
                if imghdr.what(location):
                    images.append(location)

            self._reset()
            self._load_images(images)

    def isImagePlaneFixed(self):
        return self._settings['image-plane-fixed']

    def setImagePlaneFixed(self, state):
        self._settings['image-plane-fixed'] = state
        if self._scene is None:
            return
        surface_graphics = self._scene.findGraphicsByName("plane-surfaces")
        line_graphics = self._scene.findGraphicsByName("plane-lines")
        if surface_graphics.isValid() and state:
            self._updateFixedProjectionField()
            surface_graphics.setCoordinateField(self._fixed_coordinate_field)
            line_graphics.setCoordinateField(self._fixed_coordinate_field)
        elif surface_graphics.isValid() and not state:
            self._updateAlignmentValues()
            surface_graphics.setCoordinateField(self._scaledCoordinateField)
            line_graphics.setCoordinateField(self._scaledCoordinateField)

    def isDisplayImagePlane(self):
        return self._settings['display-image-plane']

    def setImagePlaneVisible(self, state):
        self._settings['display-image-plane'] = state
        if self._scene is not None:
            self._scene.setVisibilityFlag(state)

    def getFrameCount(self):
        return self._frame_count

    def getTimeForFrameIndex(self, index, frames_per_second):
        duration = self._frame_count / frames_per_second
        frame_separation = 1 / self._frame_count
        initial_offset = frame_separation / 2

        return (index * frame_separation + initial_offset) * duration

    def getFrameIndexForTime(self, time, frames_per_second):
        duration = self._frame_count / frames_per_second
        frame_separation = 1 / self._frame_count
        initial_offset = frame_separation / 2
        return int((time / duration - initial_offset) / frame_separation + 0.5)

    def getSettings(self):
        self._settings['alignment'].update(self.getAlignSettings())
        return self._settings

    def setSettings(self, settings):
        self._settings.update(settings)
        self.setImagePlaneVisible(settings['display-image-plane'])
        self.setImagePlaneFixed(settings['image-plane-fixed'])
        if 'alignment' in settings:
            self.setAlignSettings(settings['alignment'])

    def _load_images(self, images):
        fieldmodule = self._region.getFieldmodule()
        self._frame_count = len(images)
        if self._frame_count > 0:
            # Assume all images have the same dimensions.
            width, height = get_image_size.get_image_size(images[0])
            if width != -1 or height != -1:
                node_locations = np.matrix(self.getNodeLocations())
                N = node_locations.shape[0]
                new_node_locations = np.multiply(node_locations.T, np.tile(np.matrix([width/1000.0, height/1000.0, 1.0]).T, (1, N)))
                self.setNodeLocations(new_node_locations.T.tolist())

            image_field = createVolumeImageField(fieldmodule, images)
            material = createMaterialUsingImageField(self._region, image_field)
            surface = self._scene.findGraphicsByName('plane-surfaces')
            surface.setMaterial(material)

    def getNodeLocations(self):
        fm = self._region.getFieldmodule()
        fm.beginChange()
        cache = fm.createFieldcache()
        coordinates = fm.findFieldByName('coordinates')
        nodes = fm.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_NODES)
        node_it = nodes.createNodeiterator()
        node = node_it.next()

        node_positions = []
        while node.isValid():
            cache.setNode(node)
            _, position = coordinates.evaluateReal(cache, 3)
            node_positions.append(self._getSceneTransformationFromAdjustedPosition(position))
            node = node_it.next()

        fm.endChange()

        return node_positions

    def setNodeLocations(self, node_positions):
        fm = self._region.getFieldmodule()
        fm.beginChange()
        cache = fm.createFieldcache()
        coordinates = fm.findFieldByName('coordinates')
        nodes = fm.findNodesetByFieldDomainType(Field.DOMAIN_TYPE_NODES)
        node_it = nodes.createNodeiterator()
        node = node_it.next()

        index = 0
        while node.isValid():
            cache.setNode(node)
            coordinates.assignReal(cache, self._getSceneTransformationToAdjustedPosition(list(node_positions[index])))
            index += 1
            node = node_it.next()

        fm.endChange()

    def _reset(self):
        if self._region:
            self._parent_region.removeChild(self._region)
        self._region = self._parent_region.createChild(self._region_name)
        self._scene = self._region.getScene()
        self._createModel()
        self._createGraphics()
        # self.resetAlignment()

    def _createModel(self):
        self._modelCoordinateField = createFiniteElementField(self._region)
        fieldmodule = self._region.getFieldmodule()
        self._modelScaleField = fieldmodule.createFieldConstant([1, 1, 1])
        self._scaledCoordinateField = fieldmodule.createFieldMultiply(self._modelScaleField, self._modelCoordinateField)
        self._fixed_projection_field = fieldmodule.createFieldConstant([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
        createSquare2DFiniteElement(fieldmodule, self._modelCoordinateField,
                                    [[-0.5, -0.5, 0.0], [0.5, -0.5, 0.0], [-0.5, 0.5, 0.0], [0.5, 0.5, 0.0]])

    def _createGraphics(self):
        scene = self._scene
        scene.beginChange()
        scene.removeAllGraphics()
        timekeepermodule = scene.getTimekeepermodule()
        self._timekeeper = timekeepermodule.getDefaultTimekeeper()
        fieldmodule = self._region.getFieldmodule()
        xi = fieldmodule.findFieldByName('xi')
        materialmodule = scene.getMaterialmodule()
        lines = scene.createGraphicsLines()
        lines.setExterior(True)
        lines.setName('plane-lines')
        lines.setCoordinateField(self._scaledCoordinateField)
        surfaces = scene.createGraphicsSurfaces()
        surfaces.setName('plane-surfaces')
        surfaces.setCoordinateField(self._scaledCoordinateField)
        temp1 = fieldmodule.createFieldComponent(xi, [1, 2])
        temp2 = fieldmodule.createFieldTimeValue(self._timekeeper)
        texture_field = fieldmodule.createFieldConcatenate([temp1, temp2])
        surfaces.setTextureCoordinateField(texture_field)
        surfaces.setMaterial(materialmodule.findMaterialByName('silver'))
        scene.setVisibilityFlag(self.isDisplayImagePlane())
        scene.endChange()


def tryint(s):
    try:
        return int(s)
    except:
        return s


def alphanum_key(s):
    """ Turn a string into a list of string and number chunks.
        "z23a" -> ["z", 23, "a"]
    """
    return [tryint(c) for c in re.split('([0-9]+)', s)]
