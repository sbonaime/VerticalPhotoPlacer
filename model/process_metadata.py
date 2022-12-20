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
from dataclasses import dataclass, field
from math import atan, cos, degrees, radians, sin, sqrt
from os.path import isfile, splitext

import numpy as np

from .pyexiftool import ExifTool
from .utility import meter2Degree, refConversion

TAGS =["file:imagewidth", "file:imageheight","exif:focallength",
        "exif:gpslatitude","exif:gpslatituderef","exif:gpslongitude",
        "exif:gpslongituderef", "exif:gpsaltitude","xmp:relativealtitude",
         "xmp:groundaltitude", "xmp:flightyawdegree" ,"exif:model"]
PH4RTK_TAGS =["makernotes:camerayaw","xmp:rtkflag" ]

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





def getCamSensorSize(model, img_width, img_height):
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
    rcamobj = ProcessCamera() 
    try:
        sw, sh = rcamobj.getCamsize(model)
    except CameraModelNotFound:
        sw = img_width*1.55/1000000   # 3000 -> 4.5,  close guess to some DJI camera models
        sh = img_height*1.55/1000000   # 4000 -> 6.0,  close guess to some DJI camera models
    
    return sw, sh




def createWorldfile(photo):
    """Create a worldfile for a photo.

    :return: status
    :rtype: boolean
    """

    groundalt  = photo.getAltByPriority()  

        # gpsalt 1.919  
        # baroalt  2.26
        # groundalt 1.368
        # groundalt - 0.5 => 1.589
        # groundalt - rtkheight 1.376

    
    try:
        
        #sw, sh = getCamSensorSize(camobj, img_spec.cam_model, img_spec.image_width, img_spec.image_height)

        # Sensor size needs to be in degrees, to work in EPSG:4326.
        #sensor_width_degree, sensor_height_degree = meter2Degree(photo.gpslat, photo.sensor_width_decimal, photo.sensor_height_decimal)

        scale_factor = groundalt / photo.focal_length
        sensor_pixel_width_degrees = photo.sensor_width_degree / photo.image_width
        sensor_pixel_length_degrees = photo.sensor_height_degree / photo.image_height
        img_hwidth_degrees = (photo.sensor_width_degree * scale_factor) / 2
        img_hlength_degrees = (photo.sensor_height_degree * scale_factor) / 2
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


@dataclass
class Photo:
    """
    Store frequently used metadata tags of a photo.
    """
    path: str 
    imgpath_noext : str = None
    image_width: int = None
    image_height:  int = None
    focal_length: float = None
    gpslat: float = None
    gpslon: float = None
    gpsalt: float = None
    baroalt: float = None
    groundalt: float = None
    heading: float = None
    cam_model: str = None
    #metadata: dict= field(default_factory=dict)
    sensor_width_decimal: float = None
    sensor_height_decimal : float = None
    sensor_width_degree: float = None
    sensor_height_degree: float = None
    sensor_width_decimal: float = None
    sensor_height_decimal: float = None
    rtk_metadata: dict= field(default_factory=dict)

    def __post_init__(self):
        self.imgpath_noext, ext = splitext(self.path)
        ext=ext.lower()
        self.worldfile_filename = f'{self.imgpath_noext}.{ext[1]}{ext[-1]}{WORLD_EXT}'

# {'SourceFile': '/Users/bonaime/Desktop/mayotte/100_0006_0199.JPG', 
# 'ExifTool:ExifToolVersion': 12.5, 'ExifTool:Now': '2022:11:30 17:48:53+01:00',
#  'ExifTool:NewGUID': '2022113017485301243C8D4A35914B35', 'ExifTool:FileSequence': 0, 
#  'ExifTool:Warning': '[minor] Possibly incorrect maker notes offsets (fix by 1783?)',
#   'ExifTool:ProcessingTime': 0.16753, 'File:FileName': '100_0006_0199.JPG',
#    'File:BaseName': '100_0006_0199', 'File:Directory': '/Users/bonaime/Desktop/mayotte', 
#    'File:FilePath': '/Users/bonaime/Desktop/mayotte/100_0006_0199.JPG', 'File:FileSize': 9053752, 
#    'File:FileModifyDate': '2022:11:29 17:42:59+01:00', 'File:FileAccessDate': '2022:11:30 13:10:28+01:00',
#     'File:FileInodeChangeDate': '2022:11:29 17:42:59+01:00', 'File:FilePermissions': 100700, 
#     'File:FileAttributes': '32768 0', 'File:FileDeviceNumber': 16777221, 'File:FileInodeNumber': 48138129,
#      'File:FileHardLinks': 1, 'File:FileUserID': 502, 'File:FileGroupID': 20, 'File:FileDeviceID': 0,
#       'File:FileBlockSize': 4096, 'File:FileBlockCount': 17688, 'File:FileCreateDate': '2022:11:24 10:33:57+01:00', 
#       'File:KMDItemDisplayNameWithExtensions': '100_0006_0199.JPG',
#        'File:MDItemAcquisitionMake': 'DJI', 'File:MDItemAcquisitionModel': 'FC6310R', 
#        'File:MDItemAestheticScore': 0.2359619, 'File:MDItemAltitude': -10.712, 'File:MDItemAperture': 5.31,
#         'File:MDItemBitsPerSample': 24, 'File:MDItemColorSpace': 'RGB', 
#         'File:MDItemContentCreationDate': '2022:11:24 09:26:52+01:00',
#          'File:MDItemContentCreationDate_Ranking': '2022:11:24 01:00:00+01:00',
#           'File:MDItemContentModificationDate': '2022:11:24 09:26:52+01:00', 'File:MDItemContentRating': 0,
#            'File:MDItemContentType': 'public.jpeg', 
#            'File:MDItemContentTypeTree': ['public.jpeg', 'public.image', 'public.data', 'public.item', 'public.content'],
#             'File:MDItemCreator': 'v01.09.1759', 'File:MDItemDateAdded': '2022:11:24 14:15:14+01:00', 'File:MDItemDescription':
#              'DCIM\\SURVEY\\100_0006\\100_0', 'File:MDItemDisplayName': '100_0006_0199.JPG', 'File:MDItemDocumentIdentifier': 0, 'File:MDItemEXIFGPSVersion': '2.3.0.0', 
#              'File:MDItemEXIFVersion': 2.3, 'File:MDItemExposureMode': 0, 'File:MDItemExposureProgram': 2, 'File:MDItemExposureTimeSeconds': 0.002, 'File:MDItemFlashOnOff': 0,
#               'File:MDItemFNumber': 6.3, 'File:MDItemFocalLength': 8.8, 'File:MDItemFocalLength35mm': 24, 'File:MDItemFSContentChangeDate': '2022:11:29 17:42:59+01:00', 
#               'File:MDItemFSCreationDate': '2022:11:24 10:33:57+01:00', 'File:MDItemFSCreatorCode': '', 'File:MDItemFSFinderFlags': 0, 'File:MDItemFSHasCustomIcon': '',
#                'File:MDItemFSInvisible': 0, 'File:MDItemFSIsExtensionHidden': 0, 'File:MDItemFSIsStationery': '', 'File:MDItemFSLabel': 0, 
#               'File:MDItemFSName': '100_0006_0199.JPG', 'File:MDItemFSNodeCount': '', 'File:MDItemFSOwnerGroupID': 20,
#                'File:MDItemFSOwnerUserID': 502, 'File:MDItemFSSize': 9053752, 'File:MDItemFSTypeCode': '', 'File:MDItemHasAlphaChannel': 0, 
#               'File:MDItemInterestingDate_Ranking': '2022:11:24 01:00:00+01:00', 'File:MDItemISOSpeed': 100, 'File:MDItemKind': 'Image JPEG',
#                'File:MDItemLatitude': -12.80016666666667, 'File:MDItemLogicalSize': 9053752, 'File:MDItemLongitude': 45.287415, 
#               'File:MDItemMediaAnalysisLastAttempt': '2022:11:30 13:10:24+01:00', 'File:MDItemMeteringMode': 1, 'File:MDItemOrientation': 0,
#                'File:MDItemPhotosCharacterRecognitionAnalysisVersion': '', 'File:MDItemPhotosSceneAnalysisVersion': '',
#                'File:MDItemPhysicalSize': 9056256, 'File:MDItemPixelCount': 19961856, 'File:MDItemPixelHeight': 3648, 
#               'File:MDItemPixelWidth': 5472, 'File:MDItemProfileName': 'sRGB IEC61966-2.1', 'File:MDItemRedEyeOnOff': 0,
#                'File:MDItemResolutionHeightDPI': 72, 'File:MDItemResolutionWidthDPI': 72, 'File:MDItemWhiteBalance': 1, 
#               'File:XAttrProvenance': '(Binary data 11 bytes, use -b option to extract)', 'File:FileType': 'JPEG', 'File:FileTypeExtension': 'JPG',
#                'File:MIMEType': 'image/jpeg', 'File:ExifByteOrder': 'II', 'File:ImageWidth': 5472, 'File:ImageHeight': 3648,
#                'File:EncodingProcess': 0, 'File:BitsPerSample': 8, 'File:ColorComponents': 3, 'File:YCbCrSubSampling': '2 1', 
#               'File:JPEGImageLength': 8768663, 'File:JPEGQualityEstimate': 98, 'File:JPEGDigest': 'f3235a7d187d083b7b7ead949653f730:211111', 
#               'EXIF:ImageDescription': 'DCIM\\SURVEY\\100_0006\\100_0', 'EXIF:Make': 'DJI', 
#               'EXIF:Model': 'FC6310R', 'EXIF:Orientation': 1, 'EXIF:XResolution': 72, 'EXIF:YResolution': 72, 
#               'EXIF:ResolutionUnit': 2, 'EXIF:Software': 'v01.09.1759', 'EXIF:ModifyDate': '2022:11:24 09:26:53',
#                'EXIF:YCbCrPositioning': 1, 'EXIF:ExposureTime': 0.002, 'EXIF:FNumber': 6.3, 'EXIF:ExposureProgram': 2, 
#               'EXIF:ISO': 100, 'EXIF:ExifVersion': '0230', 'EXIF:DateTimeOriginal': '2022:11:24 09:26:52', 
#               'EXIF:CreateDate': '2022:11:24 09:26:52', 'EXIF:ComponentsConfiguration': '0 3 2 1',
#                'EXIF:CompressedBitsPerPixel': 3.514181046, 'EXIF:ShutterSpeedValue': '0.00200108754498594',
#                'EXIF:ApertureValue': 6.29846381255363, 'EXIF:ExposureCompensation': 0, 'EXIF:MaxApertureValue': 2.79917173119039,
#                'EXIF:SubjectDistance': 0, 'EXIF:MeteringMode': 1, 'EXIF:LightSource': 1, 'EXIF:Flash': 32,
#                'EXIF:FocalLength': 8.8, 'EXIF:FlashpixVersion': '0010', 'EXIF:ColorSpace': 1, 'EXIF:ExifImageWidth': 5472, 
#               'EXIF:ExifImageHeight': 3648, 'EXIF:InteropIndex': 'R98', 'EXIF:InteropVersion': '0100', 'EXIF:ExposureIndex': 'undef', 
#               'EXIF:FileSource': 3, 'EXIF:SceneType': 1, 'EXIF:CustomRendered': 0, 'EXIF:ExposureMode': 0, 
#               'EXIF:WhiteBalance': 1, 'EXIF:DigitalZoomRatio': 'undef', 'EXIF:FocalLengthIn35mmFormat': 24, 
#               'EXIF:SceneCaptureType': 0, 'EXIF:GainControl': 0, 'EXIF:Contrast': 0, 'EXIF:Saturation': 0, 'EXIF:Sharpness': 0, 
#               'EXIF:SubjectDistanceRange': 0, 'EXIF:SerialNumber': '86dcc9f9dc005c450d7a4a43dacdd3fc', 'EXIF:GPSVersionID': '2 3 0 0', 'EXIF:GPSLatitudeRef': 'S',
#                'EXIF:GPSLatitude': 12.8001659444444, 'EXIF:GPSLongitudeRef': 'E', 'EXIF:GPSLongitude': 45.2874150833333,
#                'EXIF:GPSAltitudeRef': 1, 'EXIF:GPSAltitude': 10.712, 'EXIF:XPComment': 'Type=N, Mode=P, DE=None', 
#               'EXIF:XPKeywords': 'v01.09.1759;1.3.0;v1.0.0', 'EXIF:Compression': 6, 'EXIF:ThumbnailOffset': 10240, 
#               'EXIF:ThumbnailLength': 11621, 'EXIF:ThumbnailImage': '(Binary data 11621 bytes, use -b option to extract)',
#                'MakerNotes:Make': 'DJI', 'MakerNotes:SpeedX': 0.400000005960464, 'MakerNotes:SpeedY': -2.59999990463257, 
#               'MakerNotes:SpeedZ': 0, 'MakerNotes:Pitch': 1.79999995231628, 'MakerNotes:Yaw': -80.5, 'MakerNotes:Roll': 0,
#                'MakerNotes:CameraPitch': -90, 'MakerNotes:CameraYaw': -80.1999969482422, 'MakerNotes:CameraRoll': 0, 
#               'XMP:XMPToolkit': 'Image::ExifTool 12.50', 'XMP:About': 'DJI Meta Data', 'XMP:Format': 'image/jpg', 
#               'XMP:AbsoluteAltitude': -10.71, 'XMP:CalibratedFocalLength': 3666.666504, 'XMP:CalibratedOpticalCenterX': 2736.0, 
#               'XMP:CalibratedOpticalCenterY': 1824.0, 'XMP:CamReverse': 0, 'XMP:DewarpFlag': 1, 'XMP:FlightPitchDegree': '+1.80', 
#               'XMP:FlightRollDegree': '+0.00', 'XMP:FlightXSpeed': '+0.40', 'XMP:FlightYSpeed': -2.6, 
#               'XMP:FlightYawDegree': -80.5, 'XMP:FlightZSpeed': '+0.00', 'XMP:GimbalPitchDegree': -90.0, 
#               'XMP:GimbalReverse': 0, 'XMP:GimbalRollDegree': '+0.00', 'XMP:GimbalYawDegree': -80.2, 'XMP:GPSLatitude': -12.80016596,
#                'XMP:GPSLongtitude': 45.2874151, 'XMP:PhotoDiff': '34YDH3B001W2UJ20221124082712', 'XMP:RelativeAltitude': '+9.99',
#                'XMP:RtkFlag': 50, 'XMP:RtkStdHgt': 0.03674, 'XMP:RtkStdLat': 0.02068, 'XMP:RtkStdLon': 0.01977, 
#               'XMP:SelfData': 'Undefined', 'XMP:GroundAltitude': 12.09, 'XMP:Make': 'DJI', 'XMP:Model': 'FC6310R', 
#               'XMP:CreateDate': '2022:11:24', 'XMP:ModifyDate': '2022:11:24', 'XMP:AlreadyApplied': False, 'XMP:HasCrop': False, 
#               'XMP:HasSettings': False, 'XMP:Version': 7.0, 'MPF:MPFVersion': '0010', 'MPF:NumberOfImages': 2, 
#               'MPF:MPImageFlags': 8, 'MPF:MPImageFormat': 0, 'MPF:MPImageType': 65537, 'MPF:MPImageLength': 257674, 
#               'MPF:MPImageStart': 8796078, 'MPF:DependentImage1EntryNumber': 0, 'MPF:DependentImage2EntryNumber': 0, 
#               'MPF:ImageUIDList': '(Binary data 66 bytes, use -b option to extract)', 'MPF:TotalFrames': 1, 
#               'MPF:PreviewImage': '(Binary data 257674 bytes, use -b option to extract)', 'Composite:Aperture': 6.3, 
#               'Composite:ImageSize': '5472 3648', 'Composite:Megapixels': 19.961856, 'Composite:ScaleFactor35efl': 2.72727272727273, 
#               'Composite:ShutterSpeed': 0.002, 'Composite:GPSAltitude': -10.712, 'Composite:GPSLatitude': -12.8001659444444,
#                'Composite:GPSLongitude': 45.2874150833333, 'Composite:BaseName': '100_0006_0199', 
#               'Composite:CircleOfConfusion': '0.00906538606402375', 'Composite:FileExtension': 'JPG', 
#               'Composite:FileTypeDescription': 'Joint Photographic Experts Group', 'Composite:PhysicalImageSize': '76 50.6666666666667', 
#               'Composite:FOV': 73.7398575770811, 'Composite:FocalLength35efl': 24, 
#               'Composite:GPSPosition': '-12.8001659444444 45.2874150833333', 
#               'Composite:HyperfocalDistance': 1.355933812995, 'Composite:LightValue': 14.2764879418872, 
#               'Composite:BigImage': '(Binary data 257674 bytes, use -b option to extract)'}

    def get_metadata(self):
        with ExifTool() as et:
            metadata = et.get_tags(TAGS, self.path)
            #print(f'metadata {metadata}')
            # get_tags(self, tags, filename):
            #print(f"metadata {metadata}")

            if metadata['File:ImageWidth']:
                self.image_width = int(metadata['File:ImageWidth'])
            if 'File:ImageHeight'in metadata:
                self.image_height = int(metadata['File:ImageHeight'])
            if 'EXIF:FocalLength' in metadata:
                self.focal_length =float(metadata['EXIF:FocalLength'])/1000
            if 'EXIF:GPSLatitude' in metadata and  'EXIF:GPSLongitude' in metadata:
                if 'EXIF:GPSLatitudeRef' in metadata and 'EXIF:GPSLongitudeRef' in metadata:
                    self.gpslat = refConversion(metadata['EXIF:GPSLatitude'], metadata['EXIF:GPSLatitudeRef'].lower())
                    self.gpslon = refConversion(metadata['EXIF:GPSLongitude'], metadata['EXIF:GPSLongitudeRef'].lower())
            if 'EXIF:GPSAltitude' in metadata:
                self.gpsalt = float(metadata['EXIF:GPSAltitude'])
            if 'XMP:RelativeAltitude' in metadata:
                self.baroalt = float(metadata['XMP:RelativeAltitude'])
            if 'XMP:GroundAltitude' in metadata:
                self.groundalt = float(metadata['XMP:GroundAltitude'])
            if 'XMP:FlightYawDegree' in metadata:
                self.heading = float(metadata['XMP:FlightYawDegree'])
            if 'EXIF:Model' in metadata:
                self.cam_model =  str(metadata['EXIF:Model'])

            #self.metadata = metadata
            self.sensor_width_decimal, self.sensor_height_decimal = getCamSensorSize(self.cam_model,self.image_width, self.image_height)
            self.sensor_width_degree, self.sensor_height_degree = meter2Degree(self.gpslat, self.sensor_width_decimal, self.sensor_height_decimal)

            ### PH4RTK Tags
            if 'FC6310R' in self.cam_model:
                rtk_metadata = et.get_tags(PH4RTK_TAGS, self.path)
               # print(f"PH4RTK RTK data {rtk_metadata} ")
                if 'MakerNotes:CameraYaw' in rtk_metadata:
                    self.heading = float(rtk_metadata['MakerNotes:CameraYaw'])
                self.rtk_metadata = rtk_metadata

            # Worldfile does not exists
            #if not isfile(self.worldfile_filename ):
                #print(f"Creating wordfile for {self.path}")
            createWorldfile(self)
            #print(f'Photo creee :{self}')
            
    def debug(self,altitude):
            print(f'{self.imgpath_noext} altitude {altitude} photo.sensor_width_decimal {self.sensor_width_decimal} photo.sensor_height_decimal {self.sensor_height_decimal}\
            photo.focal_length {self.focal_length} photo.sensor_width_decimal {self.sensor_width_decimal}\
            photo.sensor_height_decimal {self.sensor_height_decimal} photo.gpslat {self.gpslat} photo.gpslon{self.gpslon}\
            photo.heading {self.heading} photo.worldfile_filename {self.worldfile_filename})')

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



