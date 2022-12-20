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
import math
from os.path import splitext

import numpy as np

from .utility import meter2Degree


class AltitudeNotFound(Exception):
    pass


def worldfilesGenerator(task, params):
    """Create worldfiles for a list of files, this function is to be used as a QgsTask.
    Altitude-to-use priority: Ground Altitude > Barometer Altitude > GPS Altitude

    :param task: task object automatically passed by calling function.

    :param params: list of parameters, containing:
        files: list of photo 

    :return: list of processed photos and summary of task
    :rtype: dict
    """


    print("worldfilesGenerator")
    photos = params[0]
    n_photos = len(photos)
    task.setProgress(1)

    # start creation of worldfiles
    loaded_files = list()

    for n_processed, photo in enumerate(photos):
        try:
            
            altitude = photo.getAltByPriority()
            photo.debug(altitude)
            createSingleWorldfile(photo.sensor_width_decimal, photo.sensor_height_decimal,
                                  photo.focal_length, photo.sensor_width_decimal, photo.sensor_height_decimal,
                                  photo.gpslat, photo.gpslon, altitude,
                                  photo.heading, photo.worldfile_filename)
            loaded_files.append(photo)
        except Exception:
            print(f'Erreur avec {photo.path}\n {photo}')
            continue

        percent = n_processed * 100 / n_photos 
        task.setProgress(percent)
        if task.isCanceled():
            raise Exception('Task canceled!')

    if not loaded_files:
        raise Warning("No worldfile generated. Common reasons are: \n"
                      "1. The photos does not contain heading and altitude information.\n"
                      "2. The camera sensor is not supported. \n"
                      "In case 2, please try to insert sensor name, width and height to the camlist.xml")
    
    print("end worldfilesGenerator")
    return {'files': loaded_files, 'task': task.description()}


def createSingleWorldfile(iw, ih, fl, sw, sh, lat, lon, groundalt, heading, worldfile):
    """Create a worldfile for a photo.
    :param iw: image width in pixel
    :type iw: int
    :param ih: image height in pixel
    :type ih: int
    :param fl: camera focal lenght in meter unit
    :type fl: float
    :param sw: camera sensor width in decimal unit (WGS84)
    :type sw: float
    :param sh: camera sensor height in degree unit (WGS84)
    :type sh: float
    :param lat: GPS latitude in decimal unit (WGS84)
    :type lat: float
    :param lon: GPS longitude in decimal unit (WGS84)
    :type lon: float
    :param groundalt: ground altitude in meter unit
    :type groundalt: float
    :param heading: heading angle (Flight Yaw Degree)
    :type heading: float
    :param worldfile: name of the output world file
    :type worldfile: string
    :return: status
    :rtype: boolean
    """

    try:
        # Sensor size needs to be in degrees, to work in EPSG:4326.
        sw, sh = meter2Degree(lat, sw, sh)

        scale_factor = groundalt / fl
        sensor_pixel_width_degrees = sw / iw
        sensor_pixel_length_degrees = sh / ih
        img_hwidth_degrees = (sw * scale_factor) / 2
        img_hlength_degrees = (sh * scale_factor) / 2
        ground_pixel_width = sensor_pixel_width_degrees * scale_factor
        ground_pixel_length = sensor_pixel_length_degrees * scale_factor

        # Computes upper left coordinates as required in Worldfile specification.
        hypotenuse_hlength_degrees = math.sqrt(img_hwidth_degrees * img_hwidth_degrees +
                                               img_hlength_degrees * img_hlength_degrees)
        invar_angle = math.degrees(math.atan(iw / ih))
        lat_angle = heading - invar_angle
        y_length = hypotenuse_hlength_degrees * math.cos(math.radians(lat_angle))
        x_length = hypotenuse_hlength_degrees * math.sin(math.radians(lat_angle))
        upper_left_lon = lon + x_length
        upper_left_lat = lat + y_length

        # Computes A, B, C, D parameters as required in Worldfile specification.
        A = math.cos(math.radians(heading)) * ground_pixel_width
        B = -(math.sin(math.radians(heading)) * ground_pixel_length)
        D = -(math.sin(math.radians(heading)) * ground_pixel_width)
        E = -(math.cos(math.radians(heading)) * ground_pixel_length)

        world_content = list()
        world_content.append(A)
        world_content.append(B)
        world_content.append(D)
        world_content.append(E)
        world_content.append(upper_left_lon)
        world_content.append(upper_left_lat)
        world_content = np.array(world_content)
        np.savetxt(worldfile, world_content, fmt='%1.10f', delimiter=",")

    except Exception:
        raise
