# -*- coding: utf-8 -*-
"""
/******************************************************************************************
 VerticalPhotoPlacer

 The Vertical Photo Placer Plugin for QGIS performs quick placement of
 vertical drone photos on map.
                              -------------------
        begin                : 2019-09-05
        copyright            : (C) 2019-2021 by Chubu University and
               National Research Institute for Earth Science and Disaster Resilience (NIED)
        email                : chuc92man@gmail.com
 ******************************************************************************************/

/******************************************************************************************
 *   This file is part of Vertical Photo Placer Plugin.                                   *
 *                                                                                        *
 *   This program is free software; you can redistribute it and/or modify                 *
 *   it under the terms of the GNU General Public License as published by                 *
 *   the Free Software Foundation, version 3 of the License.                              *
 *                                                                                        *
 *   Vertical Photo Placer Plugin is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or    *
 *   FITNESS FOR A PARTICULAR PURPOSE.                                                    *
 *   See the GNU General Public License for more details.                                 *
 *                                                                                        *
 *   You should have received a copy of the GNU General Public License along with         *
 *   Vertical Photo Placer Plugin. If not, see <http://www.gnu.org/licenses/>.            *
 ******************************************************************************************/
"""
from math import atan, cos, degrees, radians, sin, sqrt
from os.path import isfile, splitext

import numpy as np

from .pyexiftool import ExifTool
from .utility import meter2Degree, refConversion

TAGS =["file:imagewidth", "file:imageheight","exif:focallength",
        "exif:gpslatitude","exif:gpslatituderef","exif:gpslongitude",
        "exif:gpslongituderef", "exif:gpsaltitude","xmp:relativealtitude",
         "xmp:groundaltitude", "xmp:flightyawdegree" ,"exif:model"]

# supported file extensions
IMG_EXTS = (".jpg", ".jpeg", ".jpe", ".jfif", ".jfi", ".jif",".JPG")
WORLD_EXT = "w"

import xml.etree.ElementTree as Et

from .utility import resolveFile


class CameraModelNotFound(Exception):
    pass

class AltitudeNotFound(Exception):
    pass


class ProcessCamera:
    """Search camera sensor size from xml config file.
    """

    def __init__(self):
        
        self.tree = Et.parse(resolveFile("camlist.xml"))
        self.root = self.tree.getroot()
    
    def getCamsize(self, model):
        """Get camera size in meter unit.

        :param model: name of camera model.
        :type model: string

        :return: width and height of camera.
        :rtype: tuple
        """

        try:
            for x in self.root.findall("model"):
                if model.lower() == x.find("name").text.lower():
                    width = float(x.find("width").text) / 1000  # original width is in milimeter
                    height = float(x.find("height").text) / 1000  # original height is in milimeter
                    return width, height
            raise CameraModelNotFound('Camera model not found for: {0}. '
                                      'Please inserts the model name, sensor width and height to camlist.xml.'.
                                      format(model.lower()))
        except Exception:
            raise


def guessCamSensorSize(img_width, img_height):
    sw = img_width*1.55/1000000   # 3000 -> 4.5,  close guess to some DJI camera models
    sh = img_height*1.55/1000000   # 4000 -> 6.0,  close guess to some DJI camera models

    return sw, sh


def getCamSensorSize(rcamobj, model, img_width, img_height):
    """ Get sensor size from available list first.
    If not found, try to guess.

    :param rcamobj: ProcessCamera object
    :type rcamobj: ProcessCamera

    :param model: camera model name
    :type model: string

    :param img_width: photo width
    :type img_width: int

    :param img_height: photo height
    :type img_height: int

    :return: sensor width and height
    :rtype: tuple
    """

    try:
        sw, sh = rcamobj.getCamsize(model)
    except CameraModelNotFound:
        sw, sh = guessCamSensorSize(img_width, img_height)

    return sw, sh

def createWorldfile(photo):
    """Create a worldfile for a photo.

    :return: status
    :rtype: boolean
    """

    groundalt  = photo.getAltByPriority()

    try:
        # Sensor size needs to be in degrees, to work in EPSG:4326.
        sensor_width_degreee, sensor_height_degreee = meter2Degree(photo.gpslat, photo.sensor_width, photo.sensor_height)

        scale_factor = groundalt / photo.focal_length
        sensor_pixel_width_degrees = sensor_width_degreee / photo.image_width
        sensor_pixel_length_degrees = sensor_height_degreee / photo.image_height
        img_hwidth_degrees = (sensor_width_degreee * scale_factor) / 2
        img_hlength_degrees = (sensor_height_degreee * scale_factor) / 2
        ground_pixel_width = sensor_pixel_width_degrees * scale_factor
        ground_pixel_length = sensor_pixel_length_degrees * scale_factor

        # Computes upper left coordinates as required in Worldfile specification.
        hypotenuse_hlength_degrees = sqrt(img_hwidth_degrees * img_hwidth_degrees +
                                               img_hlength_degrees * img_hlength_degrees)
        invar_angle = degrees(atan(photo.image_width / photo.image_height))
        lat_angle = photo.heading - invar_angle
        y_length = hypotenuse_hlength_degrees * cos(radians(lat_angle))
        x_length = hypotenuse_hlength_degrees * sin(radians(lat_angle))
        upper_left_lon = photo.gpslon + x_length
        upper_left_lat = photo.gpslat + y_length

        # Computes A, B, C, D parameters as required in Worldfile specification.@
        A = cos(radians(photo.heading)) * ground_pixel_width
        B = -(sin(radians(photo.heading)) * ground_pixel_length)
        D = -(sin(radians(photo.heading)) * ground_pixel_width)
        E = -(cos(radians(photo.heading)) * ground_pixel_length)

        world_content = list()
        world_content.append(A)
        world_content.append(B)
        world_content.append(D)
        world_content.append(E)
        world_content.append(upper_left_lon)
        world_content.append(upper_left_lat)
        world_content = np.array(world_content)
        np.savetxt(photo.worldfile_filename, world_content, fmt='%1.10f', delimiter=",")

    except Exception:
        raise



class Photo:
    """
    Store frequently used metadata tags of a photo.
    """

    def __init__(self,path):

        self.path=path
        imgpath_noext, ext = splitext(self.path)
        ext=ext.lower()
        self.worldfile_filename = f'{imgpath_noext}.{ext[1]}{ext[-1]}{WORLD_EXT}'
        self.metadata = {}

    def get_metadata(self):
        with ExifTool() as et:
            metadata = et.get_tags(TAGS, self.path)
            # get_tags(self, tags, filename):
            #print(f"metadata {metadata}")
            self.image_width = int(metadata['File:ImageWidth'])
            self.image_height = int(metadata['File:ImageHeight'])
            self.focal_length = float(metadata['EXIF:FocalLength'])
            
            if metadata['EXIF:GPSLatitudeRef'] is not None and metadata['EXIF:GPSLongitudeRef']  is not None:
                self.gpslat = refConversion(metadata['EXIF:GPSLatitude'], metadata['EXIF:GPSLatitudeRef'].lower())
                self.gpslon = refConversion(metadata['EXIF:GPSLongitude'], metadata['EXIF:GPSLongitudeRef'].lower())
            
            #print(f"self.gpslat {self.gpslat} self.gpslon {self.gpslon }")

            self.gpsalt =float(metadata['EXIF:GPSAltitude'])
            self.baroalt = float(metadata['XMP:RelativeAltitude'])
            self.groundalt = float(metadata['XMP:GroundAltitude'])
            self.heading = float(metadata['XMP:FlightYawDegree'])
            self.cam_model = str(metadata['EXIF:Model'])

            self.metadata = metadata
            self.sensor_width, self.sensor_height = getCamSensorSize(ProcessCamera(), self.cam_model,self.image_width, self.image_height)
            # Worldfile does not exists
            if not isfile(self.worldfile_filename ):
                print(f"Creating wordfile for {self.path}")
                createWorldfile(self)




    def getAltByPriority(self):
        """Get altitude value, priority: Ground Altitude > Barometer Altitude > GPS Altitude
        :return: altitude value.
        :rtype: float
        """

        try:
            # determine altitude value, priority: Ground Altitude > Barometer Altitude > GPS Altitude
            if self.groundalt is not None:
                return self.groundalt
            elif self.baroalt is not None:
                return self.baroalt
            elif self.gpsalt is not None:
                return self.gpsalt


        except Exception:
            raise AltitudeNotFound("Either the input object is None type or it has no altitude information.")




class ImageMetaStore:
    """
    Store frequently used metadata tags of a photo.
    """

    def __init__(self,path):

        self.path=path
        self.metadata = {}

    def get_metadata(self):
        with ExifTool() as et:
            metadata = et.get_tags(TAGS, self.path)
            # get_tags(self, tags, filename):
            #print(f"metadata {metadata}")
            self.image_width = int(metadata['File:ImageWidth'])
            self.image_height = int(metadata['File:ImageHeight'])
            self.focal_length = float(metadata['EXIF:FocalLength'])
            
            if metadata['EXIF:GPSLatitudeRef'] is not None and metadata['EXIF:GPSLongitudeRef']  is not None:
                self.gpslat = refConversion(metadata['EXIF:GPSLatitude'], metadata['EXIF:GPSLatitudeRef'].lower())
                self.gpslon = refConversion(metadata['EXIF:GPSLongitude'], metadata['EXIF:GPSLongitudeRef'].lower())
            
            print(f"self.gpslat {self.gpslat} self.gpslon {self.gpslon }")

            self.gpsalt =float(metadata['EXIF:GPSAltitude'])
            self.baroalt = float(metadata['XMP:RelativeAltitude'])
            self.groundalt = float(metadata['XMP:GroundAltitude'])
            self.heading = float(metadata['XMP:FlightYawDegree'])
            self.cam_model = str(metadata['EXIF:Model'])

            self.metadata = metadata
#        metadata {'SourceFile': '/Users/bonaime/Desktop/mayotte/100_0006_0199.JPG', 
# 'File:ImageWidth': 5472, 'File:ImageHeight': 3648, 'EXIF:FocalLength': 8.8, 
# 'EXIF:GPSLatitude': 12.8001659444444, 'EXIF:GPSLatitudeRef': 'S', 'EXIF:GPSLongitude': 45.2874150833333,
#  'EXIF:GPSLongitudeRef': 'E', 'EXIF:GPSAltitude': 10.712, 'XMP:RelativeAltitude': '+9.99', 
#  'XMP:GroundAltitude': 12.09, 'XMP:FlightYawDegree': -80.5, 'EXIF:Model': 'FC6310R'}


        # self._image_width = None if image_width is None else int(image_width)
        # self._image_height = None if image_height is None else int(image_height)
        # self._focal_length = None if focal_length is None else float(focal_length)
        # self._gpslat = None if gpslat is None else float(gpslat)
        # self._gpslon = None if gpslon is None else float(gpslon)
        # self._gpsalt = None if gpsalt is None else float(gpsalt)
        # self._baroalt = None if baroalt is None else float(baroalt)
        # self._groundalt = None if groundalt is None else float(groundalt)
        # self._heading = None if heading is None else float(heading)
        # self._cam_model = None if cam_model is None else str(cam_model)





class ProcessMetadata:
    def __init__(self, photos):

        self.iw = "file:imagewidth"
        self.ih = "file:imageheight"
        self.fl = "exif:focallength"
        self.gpslat = "exif:gpslatitude"
        self.gpslat_ref = "exif:gpslatituderef"
        self.gpslon = "exif:gpslongitude"
        self.gpslon_ref = "exif:gpslongituderef"
        self.gpsalt = "exif:gpsaltitude"
        self.baroalt = "xmp:relativealtitude"
        self.groundalt = "xmp:groundaltitude"
        self.heading = "xmp:flightyawdegree"  # "xmp:gimbalyawdegree"
        self.cam_model = "exif:model"
        self.photos = []

        tags = [self.iw, self.ih, self.fl,
                self.gpslat, self.gpslat_ref, self.gpslon,
                self.gpslon_ref, self.gpsalt, self.baroalt,
                self.groundalt, self.heading, self.cam_model]

        for filename in photos:
            temp_photo =ImageMetaStore(path=filename)
            temp_photo.get_metadata()
            #print(f"Metadata of {filename} is {temp_photo.metadata}")
            self.photos.append(temp_photo)



        metadata = None
        with ExifTool() as et:
            metadata = et.get_tags_batch(TAGS, photos)
            metadata = [{k.lower(): v for k, v in d.items()} for d in metadata]
        
        self.metadata = metadata
        
    def filterTagFromIndex(self, idx, tag):
        """Get tag value for a single photo, search based on index."""
        try:
            return self.metadata[idx][tag]
        except Exception:
            return None
        
    def hasBaroAltitude(self):
        """Check for barometer altitude existence of the first photo"""
        try:
            baroalt = self.filterTagFromIndex(0, self.baroalt)
            if baroalt:
                return True
            else:
                return False
        except Exception:
            return False

    def getTagsByImgindex(self, idx):
        """Get tags of an photo which is identified by its index position in photo list.

        :param idx: index of the photo in the photo list.
        :type idx: int

        :return: tags value for the photo or None if error occurred.
        :rtype: ImageMetaStore or None
        """

        try:
            # latitude and longitude needs to be in global reference
            latref = self.filterTagFromIndex(idx, self.gpslat_ref)
            lonref = self.filterTagFromIndex(idx, self.gpslon_ref)
            lat = self.filterTagFromIndex(idx, self.gpslat)
            lon = self.filterTagFromIndex(idx, self.gpslon)
            if latref is not None and lonref is not None:
                lat = refConversion(lat, latref.lower())
                lon = refConversion(lon, lonref.lower())

            tags_data = {
                'image_width': self.filterTagFromIndex(idx, self.iw),
                'image_height': self.filterTagFromIndex(idx, self.ih),
                'focal_length': self.filterTagFromIndex(idx, self.fl) / 1000,
                'gpslat': lat,
                'gpslon': lon,
                'gpsalt': self.filterTagFromIndex(idx, self.gpsalt),
                'baroalt': self.filterTagFromIndex(idx, self.baroalt),
                'groundalt': self.filterTagFromIndex(idx, self.groundalt),
                'heading': self.filterTagFromIndex(idx, self.heading),
                'cam_model': self.filterTagFromIndex(idx, self.cam_model),
            }

            img_meta = ImageMetaStore(**tags_data)
            return img_meta

        except Exception:
            return None

    def getTagsAllImgs(self):
        """Get tags for all photos.

        :return: list of ImageMetaStore instances.
        :rtype: list
        """
        print
        #imgsmeta = [self.getTagsByImgindex(i) for i in range(len(self.metadata))]
        #print(f"imgsmeta {imgsmeta}")
        print(f"self.photos {self.photos}")
        
        return self.photos
