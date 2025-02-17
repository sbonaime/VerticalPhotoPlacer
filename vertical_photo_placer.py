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
import os.path
from enum import Enum

from qgis.core import Qgis, QgsProject, QgsRasterTransparency, QgsTask, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform
from qgis.gui import QgsMapToolEmitPoint

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication, QFileInfo, Qt
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import QAction, QFileDialog, QMessageBox, QGraphicsScene, QFrame, QGraphicsPixmapItem

# Initialize Qt resources from file resources.py
from .resources import *
# Import the code for the dialog
from .vertical_photo_placer_dialog import VerticalPhotoPlacerDialog

from .model.process_metadata import ProcessMetadata
from .model.process_camera import ProcessCamera, getCamSensorSize
from .model.utility import getPhotos, getDSMValbyCoors, getGroundsize, meter2Degree, \
    computeHomepTerrAltfromAdjPhotosMatching, getWorldfileExistPhotos
from .model.altitude_adjuster import loadPhotosMetadata, altitudeAdjusterAdjacent, \
    altitudeAdjusterHome, altitudeAdjusterTerrain
from .model.uav_georeference import worldfilesGenerator

from .ui.pixmap_item import PixmapItem
from .ui.input_dialog import InputDialog


# parameters to be used in displaying photos in adjacent photo matching panel
DISPLAY_RES = 750
SENSOR_WIDTH = 12
SENSOR_HEIGHT = 13
DIFF_LAT = 14
DIFF_LON = 15

# base maps
BASEOSM = 'type=xyz&url=http://a.tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0&crs=EPSG3857'
BASEGOOGLE = 'type=xyz&url=http://www.google.cn/maps/vt?lyrs%3Ds@189%26gl%3Dcn%26x%3D%7Bx%7D%26y%3D%7By%7D%26z%3D%7Bz%7D&zmax=18&zmin=0&crs=EPSG3857'


class InvalidRasterLayer(Exception):
    pass


class CountTasks(Enum):
    QUICKVIEW = 2
    HOMEPOINT = 3
    ADJMATCHING = 3
    SIMPLE = 3


def showDialog(window_title, dialog_text, icon_level):
    dialog = QMessageBox()
    dialog.setSizeGripEnabled(True)
    dialog.setWindowTitle(window_title)
    dialog.setText(dialog_text)
    dialog.setIcon(icon_level)
    dialog.exec_()


def showDEMNotSpecified():
    showDialog(window_title="Error: No digital elevation file is specified!",
               dialog_text="This feature requires a digital elevation file. \n"
                           "Please specify one or switch to Quick view.",
               icon_level=QMessageBox.Critical)


def showBarometerAltNotFound():
    showDialog(window_title="Error: Barometer altitude is not found!",
               dialog_text="Input photos have no barometer altitude. \n"
                           "Please select Quick view or Simple correction to continue.",
               icon_level=QMessageBox.Critical)


def showHomepointNotSpecified():
    showDialog(window_title="Warning: No home point is specified!",
               dialog_text="This method requires a home point location. \n"
                           "Please specify one.",
               icon_level=QMessageBox.Critical)


def showAltitudeOffsetNotSpecified():
    showDialog(window_title="Warning: Altitude offset is not set!",
               dialog_text="This method requires an altitude offset. \n"
                           "Please selects two adjacent photos and slides to create one.",
               icon_level=QMessageBox.Critical)


class VerticalPhotoPlacer:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor.

        :param iface: An interface instance that will be passed to this class
            which provides the hook by which you can manipulate the QGIS
            application at run time.
        :type iface: QgsInterface
        """

        # Save reference to the QGIS interface
        self.iface = iface
        self.canvas = self.iface.mapCanvas()
        # self.canvas.mapRenderer().setDestinationCrs(QgsCoordinateReferenceSystem(qgis.utils.iface.activeLayer().crs().toWkt()))

        # initialize plugin directory
        self.plugin_dir = os.path.dirname(__file__)
        # initialize locale
        locale = QSettings().value('locale/userLocale')[0:2]
        self.iface.messageBar().pushMessage("Info", "Locale {0}".format(locale), level=Qgis.Info, duration=5)
        locale_path = os.path.join(
            self.plugin_dir,
            'i18n',
            'VerticalPhotoPlacer_{}.qm'.format(locale))

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        # Declare instance attributes
        self.actions = []
        self.menu = self.tr(u'&Vertical Photo Placer')

        # altitude adjuster variables
        self.img_folder = ""
        self.dem_path = ""
        self.homepoint_map_pointer = QgsMapToolEmitPoint(self.canvas)
        self.homepoint_lat = None
        self.homepoint_lon = None
        self.homepoint_alt = None
        self.overlap_imgs = [None, None]
        self.alt_corval = None
        self.adj_scene = QGraphicsScene()
        self.adj_item1 = None
        self.adj_item2 = None
        self.adj_img1_spec = None
        self.adj_img2_spec = None
        self.adj_scaleX2 = None
        self.alt_task = None

        self.workflow_ntasks = None
        self.progress_track = None

        # supported file extensions
        self.img_exts = (".jpg", ".jpeg", ".jpe", ".jfif", ".jfi", ".jif")
        self.world_ext = "w"

    # noinspection PyMethodMayBeStatic
    def tr(self, message):
        """Get the translation for a string using Qt translation API.

        We implement this ourselves since we do not inherit QObject.

        :param message: String for translation.
        :type message: str, QString

        :returns: Translated version of message.
        :rtype: QString
        """
        # noinspection PyTypeChecker,PyArgumentList,PyCallByClass
        return QCoreApplication.translate('VerticalPhotoPlacer', message)

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None):
        """Add a toolbar icon to the toolbar.

        :param icon_path: Path to the icon for this action. Can be a resource
            path (e.g. ':/plugins/foo/bar.png') or a normal file system path.
        :type icon_path: str

        :param text: Text that should be shown in menu items for this action.
        :type text: str

        :param callback: Function to be called when the action is triggered.
        :type callback: function

        :param enabled_flag: A flag indicating if the action should be enabled
            by default. Defaults to True.
        :type enabled_flag: bool

        :param add_to_menu: Flag indicating whether the action should also
            be added to the menu. Defaults to True.
        :type add_to_menu: bool

        :param add_to_toolbar: Flag indicating whether the action should also
            be added to the toolbar. Defaults to True.
        :type add_to_toolbar: bool

        :param status_tip: Optional text to show in a popup when mouse pointer
            hovers over the action.
        :type status_tip: str

        :param parent: Parent widget for the new action. Defaults None.
        :type parent: QWidget

        :param whats_this: Optional text to show in the status bar when the
            mouse pointer hovers over the action.

        :returns: The action that was created. Note that the action is also
            added to self.actions list.
        :rtype: QAction
        """

        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)

        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            # Adds plugin icon to Plugins toolbar
            self.iface.addToolBarIcon(action)

        if add_to_menu:
            self.iface.addPluginToRasterMenu(
                self.menu,
                action)

        self.actions.append(action)

        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""

        icon_path = ':/plugins/vertical_photo_placer/icon.png'
        self.add_action(
            icon_path,
            text=self.tr(u'Vertical Photo Placer'),
            callback=self.run,
            parent=self.iface.mainWindow())

        # will be set False in run()
        self.dlg = VerticalPhotoPlacerDialog()

        self.dlg.input_folder.textChanged.connect(self.onInputFolderChanged)
        self.dlg.input_button.clicked.connect(self.onSelectPhotoFolder)
        self.dlg.input_dem.textChanged.connect(self.onDEMChange)
        self.dlg.dem_button.clicked.connect(self.onSelectDEM)
        self.dlg.dem_widget.hide()
        self.dlg.close_button.clicked.connect(self.onClose)
        self.dlg.ok_button.clicked.connect(self.onExecute)
        self.dlg.cancel_button.clicked.connect(self.onCancel)

        self.dlg.alt_corr_method.currentIndexChanged.connect(self.onSelectAltCorrMethod)
        self.homepoint_map_pointer.canvasClicked.connect(self.onSetHomepoint)
        self.dlg.homepoint_longitude.textChanged.connect(self.onCoorChanged)
        self.dlg.homepoint_latitude.textChanged.connect(self.onCoorChanged)
        self.dlg.adjphotos_select_button.clicked.connect(self.onSelectAdjacentPhotos)
        self.dlg.adjphotos_clear_button.clicked.connect(self.onClearAdjacentPhotos)
        self.dlg.adjphotos_slider.valueChanged.connect(self.onSliderValueChanged)
        self.dlg.adjphotos_graphics_view.setFrameShape(QFrame.NoFrame)
        self.dlg.adjphotos_graphics_view.setScene(self.adj_scene)
        self.dlg.adjphotos_setrange_button.clicked.connect(self.onAltSetrange)

        self.setupWelcomePhoto()

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginRasterMenu(
                self.tr(u'&Vertical Photo Placer'),
                action)
            self.iface.removeToolBarIcon(action)

    """ UI managing funtions"""
    def onInputFolderChanged(self):
        self.img_folder = self.dlg.input_folder.text()
        self.onClearAdjacentPhotos()
        self.dlg.progress_bar.setValue(0)

    def onSelectPhotoFolder(self):
        folder = QFileDialog.getExistingDirectory(self.dlg, "Select folder ")
        # if user do not select any folder, then don't change folder_name
        if len(folder) > 1:
            self.dlg.input_folder.setText(folder)

    def onDEMChange(self):
        self.dem_path = self.dlg.input_dem.text()
        self.homepoint_alt = None
        self.dlg.homepoint_elevation.setText(str(self.homepoint_alt))
        self.dlg.progress_bar.setValue(0)

    def onSelectDEM(self):
        filename, _filter = QFileDialog.getOpenFileName(self.dlg, "Select DEM file ", "", '*.tif')
        # prevent assigning DEM to ""
        if len(filename) > 1:
            self.dlg.input_dem.setText(filename)

    def onSelectAdjacentPhotos(self):
        """Visualize two photos to the plugin UI so that geometric relationship of the photos are preserved."""

        filename, _filter = QFileDialog.getOpenFileNames(self.dlg, "Select two overlapped photos ",
                                                         self.img_folder,
                                                         "Images ({0})".format(" ".join(list("*"+i for i in self.img_exts))))
        self.dlg.progress_bar.setValue(0)

        if len(filename) == 2:
            self.adj_scene.clear()
            self.alt_corval = None
            self.overlap_imgs = filename

            try:
                specs = ProcessMetadata(self.overlap_imgs).getTagsAllImgs()

                pix1 = QPixmap(self.overlap_imgs[0]).scaled(DISPLAY_RES, DISPLAY_RES, Qt.KeepAspectRatio)
                pix2 = QPixmap(self.overlap_imgs[1]).scaled(DISPLAY_RES, DISPLAY_RES, Qt.KeepAspectRatio)

                self.adj_item1 = PixmapItem(pix1)
                self.adj_item2 = PixmapItem(pix2)

                self.adj_item1.setTransformOriginPoint(pix1.rect().center())
                self.adj_item2.setTransformOriginPoint(pix2.rect().center())

                self.adj_item1.setRotation(specs[0].heading)
                self.adj_item2.setRotation(specs[1].heading)

                diff_lat = specs[0].gpslat - specs[1].gpslat
                diff_lon = specs[0].gpslon - specs[1].gpslon

                sw, sh = getCamSensorSize(ProcessCamera(), specs[0].cam_model,
                                          specs[0].image_width, specs[0].image_height)
                sw, sh = meter2Degree(specs[0].gpslat, sw, sh)

                # set position img 1
                ground_X1, ground_Y1 = getGroundsize(specs[0].image_width, specs[0].image_height,
                                                     sw, sh,
                                                     specs[0].focal_length,
                                                     specs[0].baroalt)
                ratio = float(DISPLAY_RES / max(specs[0].image_width, specs[0].image_height))
                count_Y = int((diff_lat / ground_Y1)*ratio)
                count_X = int((diff_lon / ground_X1)*ratio)
                X_ul, Y_ul = 0, 0
                self.adj_item1.setPos(X_ul, Y_ul)

                # set position img 2
                ground_X2, ground_Y2 = getGroundsize(specs[1].image_width, specs[1].image_height,
                                                     sw, sh,
                                                     specs[1].focal_length,
                                                     specs[1].baroalt)
                self.adj_scaleX2 = float(ground_X2/ground_X1)
                self.adj_item2.setScale(self.adj_scaleX2)
                X_ul, Y_ul = -count_X, count_Y
                self.adj_item2.setPos(X_ul, Y_ul)

                self.adj_scene.addItem(self.adj_item1)
                self.adj_scene.addItem(self.adj_item2)

                self.adj_img1_spec = specs[0]
                self.adj_img1_spec.sensor_width = sw
                self.adj_img1_spec.sensor_height = sh
                self.adj_img1_spec.diff_lat = diff_lat
                self.adj_img1_spec.diff_lon = diff_lon

                self.adj_img2_spec = specs[1]

                # shrink QGraphicScene to items
                self.adj_scene.setSceneRect(self.adj_scene.itemsBoundingRect())

            except Exception:
                self.iface.messageBar().pushMessage("Notice",
                                                    "Please check if the photos contain heading "
                                                    "and barometer altitude information!",
                                                    level=Qgis.Info,
                                                    duration=5)

    def onClearAdjacentPhotos(self):
        """Reset state of adjacent photos matching widget and variables."""

        self.adj_scene.clear()
        self.adj_item1 = None
        self.adj_item2 = None
        self.adj_scaleX2 = None
        self.adj_img1_spec = None
        self.alt_corval = None
        self.overlap_imgs = [None, None]

    def onSelectAltCorrMethod(self):
        method_index = self.dlg.alt_corr_method.currentIndex()
        if method_index == 0:
            self.canvas.unsetMapTool(self.homepoint_map_pointer)
            self.dlg.alt_stackedwidget.setCurrentIndex(0)
            self.dlg.dem_widget.hide()
        elif method_index == 1:
            self.canvas.setMapTool(self.homepoint_map_pointer)
            self.dlg.alt_stackedwidget.setCurrentIndex(1)
            self.dlg.input_dem_label.setText("Input DEM (required)")
            self.dlg.dem_widget.show()
        elif method_index == 2:
            self.canvas.setMapTool(self.homepoint_map_pointer)
            self.dlg.alt_stackedwidget.setCurrentIndex(2)
            self.dlg.input_dem_label.setText("Input DEM (not required but recommended)")
            self.dlg.dem_widget.show()
        else:
            self.canvas.unsetMapTool(self.homepoint_map_pointer)
            self.dlg.alt_stackedwidget.setCurrentIndex(0)
            self.dlg.input_dem_label.setText("Input DEM (required)")
            self.dlg.dem_widget.show()

    def onSetHomepoint(self, point, button):
        """Get X and Y coordinates of clicked point and convert to EPSG:4326.

        :param point: passed object.
            Contain X and Y coordinates of clicked point, in the current CRS.
        :param button: passed object
        """

        try:
            canvasCRS = self.iface.mapCanvas().mapRenderer().destinationCrs()
        except Exception:
            canvasCRS = self.iface.mapCanvas().mapSettings().destinationCrs()
        epsg4326 = QgsCoordinateReferenceSystem('EPSG:4326')
        transform = QgsCoordinateTransform(canvasCRS, epsg4326, QgsProject.instance())
        pt4326 = transform.transform(point.x(), point.y())

        self.dlg.homepoint_longitude.setText(str(pt4326.x()))
        self.dlg.homepoint_latitude.setText(str(pt4326.y()))

    def onCoorChanged(self):
        """Update the plugin UI with X, Y coordinates."""

        lon_text = self.dlg.homepoint_longitude.text()
        lat_text = self.dlg.homepoint_latitude.text()
        try:
            lon_text = float(lon_text)
            lat_text = float(lat_text)
        except ValueError:
            lon_text = None
            lat_text = None

        self.homepoint_lon = lon_text
        self.homepoint_lat = lat_text

        self.updateHomeAltText(self.homepoint_lon, self.homepoint_lat)

    def updateHomeAltText(self, lon, lat):
        """Update the plugin UI with DEM altitude of home point

        :param lon: Longitude coordinate.
        :type lon: float

        :param lat: Latitude coordinate.
        :type lat: float
        """

        if lon is not None:
            if os.path.isfile(self.dem_path):
                self.homepoint_alt = getDSMValbyCoors(self.dem_path, [lon, lat])
                self.dlg.homepoint_elevation.setText(str(self.homepoint_alt))
        else:
            self.homepoint_alt = None
            self.dlg.homepoint_elevation.setText(str("None"))

    def onAltSetrange(self):
        w = InputDialog()
        if w.exec():
            val = 10*w.getInputs()
            self.dlg.adjphotos_slider.setMaximum(val)
            self.dlg.adjphotos_slider.setMinimum(0-val)
            self.dlg.alt_scale_maxlabel.setText("{0}m".format(val/10))
            self.dlg.alt_scale_minlabel.setText("-{0}m".format(val/10))

    def onSliderValueChanged(self):
        if self.overlap_imgs[0] is not None and self.overlap_imgs[1] is not None:
            altval = self.dlg.adjphotos_slider.value()/10
            self.adjustPhoto2Geometry(altval)
            self.alt_corval = altval

    def adjustPhoto2Geometry(self, altval):
        """Adjust geometry of photo 2 after changing altitude.
        Adjustion is made in relation to photo 1.

        :param altval: Altitude offset change.
        :type altval: float
        """

        ground_X1, ground_Y1 = getGroundsize(self.adj_img1_spec.image_width,
                                              self.adj_img1_spec.image_height,
                                              self.adj_img1_spec.sensor_width,
                                              self.adj_img1_spec.sensor_height,
                                              self.adj_img1_spec.focal_length,
                                              self.adj_img1_spec.baroalt + altval)
        ratio = float(DISPLAY_RES / max(self.adj_img1_spec.image_width,
                                        self.adj_img1_spec.image_height))
        count_Y = int((self.adj_img1_spec.diff_lat / ground_Y1)*ratio)
        count_X = int((self.adj_img1_spec.diff_lon / ground_X1)*ratio)
        self.adj_item2.setPos(-count_X, count_Y)

    def onCancel(self):
        """Cancel task execution."""
        try:
            self.alt_task.cancel()
            return 0
        except Exception:
            return 1

    def onClose(self):
        """Close plugin."""
        self.dlg.close()

    def setupWelcomePhoto(self):
        scene = QGraphicsScene()
        self.dlg.img_placeholder.setScene(scene)
        pixitem = QGraphicsPixmapItem(QPixmap(':/plugins/vertical_photo_placer/icon.png'))
        scene.addItem(pixitem)
        scene.setSceneRect(scene.itemsBoundingRect())

    """ Processing functions"""
    def onExecute(self):
        """Perform altitude correction and display photos when user click OK button.
        All photos of the folder are used.
        """

        if not os.path.isdir(self.img_folder):
            showDialog(window_title="Error: Invalid Input",
                       dialog_text="Please enter a valid input folder",
                       icon_level=QMessageBox.Critical)
            return

        photos = getPhotos(self.img_folder, self.img_exts)
        if not photos:
            self.iface.messageBar().pushMessage("Notice", "No photo found!", level=Qgis.Info, duration=5)
            return

        method_index = self.dlg.alt_corr_method.currentIndex()
        if method_index == 0:
            self.quickView(photos)
        elif method_index == 1:
            self.homepointCorrectionView(photos)
        elif method_index == 2:
            self.adjacentPhotoMatchingView(photos)
        else:
            self.simpleCorrectionView(photos)

    def setupProgressTrackingWf(self, n_tasks):
        self.workflow_ntasks = n_tasks
        self.progress_track = [(i * 100) / self.workflow_ntasks for i in range(self.workflow_ntasks)]

    def loadPhotosMetadataTask(self, photos, callback):
        """Loads metadata from photos.
        This uses Pyexiftool.
        This can be a long-running task when a large number of photos are loaded.

        :param photos: list of fullpath to photos.
        :type photos: list

        :param callback: function to be called next.
        :type callback: function object
        """
        start_progress = self.progress_track[0]
        self.alt_task = QgsTask.fromFunction('Load photos metadata',
                                             loadPhotosMetadata,
                                             params=[photos],
                                             on_finished=callback,
                                             flags=QgsTask.CanCancel)
        self.alt_task.progressChanged.connect(lambda: self.dlg.progress_bar.setValue(
            int(start_progress + self.alt_task.progress()/self.workflow_ntasks)))
        QgsApplication.taskManager().addTask(self.alt_task)

    def quickView(self, photos):
        self.iface.messageBar().pushMessage("Info", "Performs quick view!", level=Qgis.Info, duration=5)
        self.setupProgressTrackingWf(CountTasks.QUICKVIEW.value)
        photo_with_worldfile = getWorldfileExistPhotos(photos, self.world_ext)
        if photo_with_worldfile:
            inputs = {'files': photo_with_worldfile, 'task': None}
            self.dlg.progress_bar.setValue(100)
            self.onCreateWorldfileCompleted(exception=None, result=inputs)
        else:
            self.loadPhotosMetadataTask(photos, self.createWorldfile)

    def simpleCorrectionView(self, photos):
        if not os.path.isfile(self.dem_path):
            showDEMNotSpecified()
            return

        def altitudeAdjusterTerrainTask(exception, result=None):
            if exception:
                showDialog(window_title="Warning: Processing exited!",
                           dialog_text="{0}".format(str(exception)),
                           icon_level=QMessageBox.Warning)
            else:
                self.progress_track.pop(0)
                start_progress = self.progress_track[0]

                files = list(result["files"])
                imgsmeta = result["imgsmeta"]
                self.alt_task = QgsTask.fromFunction('Adjust GPS altitude based on terrain height substraction',
                                                     altitudeAdjusterTerrain,
                                                     params=[files, imgsmeta, self.dem_path],
                                                     on_finished=self.createWorldfile)
                self.alt_task.progressChanged.connect(lambda: self.dlg.progress_bar.setValue(
                    int(start_progress + self.alt_task.progress() / self.workflow_ntasks)))
                QgsApplication.taskManager().addTask(self.alt_task)

        # start from loading photos metadata
        self.iface.messageBar().pushMessage("Info", "Performs Simple correction and View!", level=Qgis.Info, duration=5)
        self.setupProgressTrackingWf(CountTasks.SIMPLE.value)
        self.loadPhotosMetadataTask(photos, altitudeAdjusterTerrainTask)

    def homepointCorrectionView(self, photos):
        if not ProcessMetadata([photos[0]]).hasBaroAltitude():
            showBarometerAltNotFound()
            return

        if not os.path.isfile(self.dem_path):
            showDEMNotSpecified()
            return

        if self.homepoint_alt is None:
            showHomepointNotSpecified()
            return

        def altitudeAdjusterHomeTask(exception, result=None):
            if exception:
                showDialog(window_title="Warning: Processing exited!",
                           dialog_text="{0}".format(str(exception)),
                           icon_level=QMessageBox.Warning)
            else:
                self.progress_track.pop(0)
                start_progress = self.progress_track[0]

                files = list(result["files"])
                imgsmeta = result["imgsmeta"]
                self.iface.messageBar().pushMessage("Notice",
                                                    "Home point terrain altitude: {0} meters".format(self.homepoint_alt),
                                                    level=Qgis.Info,
                                                    duration=5)
                self.alt_task = QgsTask.fromFunction('Adjust altitude based on home point',
                                                     altitudeAdjusterHome,
                                                     params=[self.homepoint_alt, files, imgsmeta, self.dem_path],
                                                     on_finished=self.createWorldfile)
                self.alt_task.progressChanged.connect(lambda: self.dlg.progress_bar.setValue(
                    int(start_progress + self.alt_task.progress() / self.workflow_ntasks)))
                QgsApplication.taskManager().addTask(self.alt_task)

        # start from loading photos metadata
        self.iface.messageBar().pushMessage("Info", "Performs Homepoint correction and View!", level=Qgis.Info, duration=5)
        self.setupProgressTrackingWf(CountTasks.HOMEPOINT.value)
        self.loadPhotosMetadataTask(photos, altitudeAdjusterHomeTask)

    def adjacentPhotoMatchingView(self, photos):
        if not ProcessMetadata([photos[0]]).hasBaroAltitude():
            showBarometerAltNotFound()
            return

        if self.alt_corval is None:
            showAltitudeOffsetNotSpecified()
            return

        # parameters to be used in altitudeAdjusterHomeTask
        home_terrain_alt, adj_terrain_alt_avg = \
            computeHomepTerrAltfromAdjPhotosMatching(self.dem_path,
                                                     [self.adj_img1_spec.gpslon, self.adj_img1_spec.gpslat],
                                                     [self.adj_img2_spec.gpslon, self.adj_img2_spec.gpslat],
                                                     self.alt_corval)

        def altitudeAdjusterAdjacentTask(exception, result=None):
            if exception:
                showDialog(window_title="Warning: Processing exited!",
                           dialog_text="{0}".format(str(exception)),
                           icon_level=QMessageBox.Warning)
            else:
                self.progress_track.pop(0)
                start_progress = self.progress_track[0]

                files = list(result["files"])
                imgsmeta = result["imgsmeta"]
                # if all conditions passed, proceed.
                self.iface.messageBar().pushMessage("Notice",
                                                    "Altitude offset: {0} meters".format(self.alt_corval),
                                                    level=Qgis.Info,
                                                    duration=5)
                self.alt_task = QgsTask.fromFunction('Adjust altitude based on photos matching',
                                                     altitudeAdjusterAdjacent,
                                                     params=[files,
                                                             imgsmeta,
                                                             home_terrain_alt,
                                                             adj_terrain_alt_avg,
                                                             self.dem_path],
                                                     on_finished=self.createWorldfile)
                self.alt_task.progressChanged.connect(lambda: self.dlg.progress_bar.setValue(
                    int(start_progress + self.alt_task.progress() / self.workflow_ntasks)))
                QgsApplication.taskManager().addTask(self.alt_task)

        # start from loading photos metadata
        self.iface.messageBar().pushMessage("Info", "Performs Adjacent photos matching and View!", level=Qgis.Info,
                                            duration=5)
        self.setupProgressTrackingWf(CountTasks.ADJMATCHING.value)
        self.loadPhotosMetadataTask(photos, altitudeAdjusterAdjacentTask)

    def createWorldfile(self, exception, result=None):
        """Generate worldfile for each input photo.

        :param exception: Passed object from the calling function.
        :type exception: Exception

        :param result: Contain a list of path of photos.
            The default is None.
        :type result: dict
        """

        if exception:
            showDialog(window_title="Warning: Processing exited!",
                       dialog_text="{0}".format(str(exception)),
                       icon_level=QMessageBox.Warning)
        else:
            self.progress_track.pop(0)
            start_progress = self.progress_track[0]

            files = list(result["files"])
            imgsmeta = result["imgsmeta"]
            self.alt_task = QgsTask.fromFunction('Generate worldfile',
                                                 worldfilesGenerator,
                                                 params=[files, imgsmeta, self.world_ext],
                                                 on_finished=self.onCreateWorldfileCompleted)
            self.alt_task.progressChanged.connect(lambda: self.dlg.progress_bar.setValue(
                int(start_progress + self.alt_task.progress()/self.workflow_ntasks)))
            self.alt_task.taskTerminated.connect(lambda: self.dlg.progress_bar.reset())
            QgsApplication.taskManager().addTask(self.alt_task)

    def onCreateWorldfileCompleted(self, exception, result=None):
        """Load layers.

        First, remove layers if already loaded, then reload again.
        There are probably better ways to do this, such as just refreshing layers.
        Unfortunately, I am not aware of at the time of writing this plugin.

        This method was tried, but no luck!
        # raster = self.iface.activeLayer()
        # raster.dataProvider().reloadData()
        # raster.triggerRepaint()
        # self.iface.mapCanvas().refresh()
        # self.iface.layerTreeView().refreshLayerSymbology(raster.id())

        :param exception: Passed object from the calling function.
        :type exception: Exception

        :param result: Contain a list of path of photos.
            The default is None.
        :type result: dict
        """

        if exception is None:
            VerticalPhotoPlacer.removeDupLayers(result["files"])
            self.loadBasemap()
            self.loadLayers(result["files"])
        else:
            self.iface.messageBar().pushMessage("Notice", str(exception), level=Qgis.Info, duration=5)

    @staticmethod
    def removeDupLayers(files):
        """Remove duplicated layers on canvas that have the same source data.

        :param files: Fullpath list of source data to compare.
        :type files: list
        """

        files = [os.path.realpath(f) for f in files]
        selected_layer = QgsProject.instance().mapLayers().values()
        for layer in selected_layer:
            if os.path.realpath(layer.source()) in files:
                try:
                    QgsProject.instance().removeMapLayers([layer.id()])
                except Exception:
                    continue

    def loadLayers(self, images):
        """Load photos as raster layers.

        :param images: Fullpath list of photos.
        :type images: list
        """

        if images is None:
            self.iface.messageBar().pushMessage("Notice",
                                                "No valid vertical photo found in the selected photos/folder",
                                                level=Qgis.Info,
                                                duration=3)
            return

        try:
            # To prevent QGIS from raising Coordinate Reference System Selector popup
            s = QSettings()
            default_value = s.value("/Projections/defaultBehaviour")
            s.setValue("/Projections/defaultBehaviour", "useProject")

            first_img = None
            count = 0
            for img in images:
                status = self.loadGeotagImage(img)
                if status:
                    self.iface.messageBar().clearWidgets()
                    count = count + 1
                    if first_img is None:
                        first_img = img

            self.zoomLayer(first_img)
            self.iface.messageBar().pushMessage("Success", "Loaded {0} photos".format(count),
                                                level=Qgis.Success,
                                                duration=3)
        except Exception:
            raise
        finally:
            s.setValue("/Projections/defaultBehaviour", default_value)

    def loadBasemap(self):
        """Load a basemap if not loaded yet, Google Satellite >> OSM"""

        sources = [layer.source() for layer in QgsProject.instance().mapLayers().values()]
        for source in sources:
            if 'xyz&url' in source:
                return

        try:
            self.iface.addRasterLayer(BASEGOOGLE, 'Google Satellite', "wms")
        except Exception:
            try:
                self.iface.addRasterLayer(BASEOSM, 'Open Street Map', "wms")
            except Exception:
                self.iface.messageBar().pushMessage("Warning", "Cannot load basemap",
                                                    level=Qgis.Warning,
                                                    duration=3)
                return

    def loadGeotagImage(self, imgname):
        """Load a photo as raster layer.

        :param imgname: Fullpath of the photo.
        :type imgname: string

        :return: success code
        :rtype: int
        """

        try:
            rt = QgsRasterTransparency()
            rt.initializeTransparentPixelList(0, 0, 0)
            file_info = QFileInfo(imgname)
            fbasename = file_info.baseName()
            rlayer = self.iface.addRasterLayer(imgname, fbasename)
            crs = rlayer.crs()
            crs.createFromId(4326)
            rlayer.setCrs(crs)
            rlayer.renderer().setRasterTransparency(rt)

            if not rlayer.isValid():
                raise InvalidRasterLayer("Cannot load {0}.".format(imgname))

        except Exception:
            self.iface.messageBar().pushMessage("Notice",
                                                "ERROR: Photo {0} failed to load".format(imgname),
                                                level=Qgis.Info,
                                                duration=5)
            return False

        return True

    def zoomLayer(self, img):
        """Zoom to the layer created from an image.

        :param img: Fullpath to an image.
        :type img: string
        """

        selected_layer = QgsProject.instance().mapLayers().values()
        for layer in selected_layer:
            if layer.source() == img:
                self.iface.mapCanvas().setExtent(layer.extent())
                break

    def run(self):
        """Run method that performs all the real work"""
        self.dlg.show()
