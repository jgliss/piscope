# -*- coding: utf-8 -*-
"""
Pyplis high level test module

This module contains some highlevel tests with the purpose to ensure 
basic functionality of the most important features for emission-rate 
analyses.

Note
----
The module is based on the dataset "testdata_minimal" which can be found
in the GitHub repo in the folder "pyplis/data/". The dataset is based on 
the official Pyplis testdata set which is used for the example scripts. 
This minimal version does not contain all images and the images are reduced
in size (Gauss-pyramid level 4).

Author: Jonas Gliß
Email: jonasgliss@gmail.com
License: GPLv3+
"""
from __future__ import division

from pyplis import Dataset, __dir__, Filter, Camera, Source, MeasSetup,\
    CellCalibEngine, Img, OptflowFarneback, PlumeBackgroundModel,\
    LineOnImage
from os.path import join, exists
from datetime import datetime
from numpy.testing import assert_almost_equal, assert_allclose
import pytest

BASE_DIR = join(__dir__, "data", "testdata_minimal")
IMG_DIR = join(BASE_DIR, "images")

START_PLUME = datetime(2015, 9, 16, 7, 10, 00)
STOP_PLUME = datetime(2015, 9, 16, 7, 20, 00)

START_CALIB = datetime(2015, 9, 16, 6, 59, 00)
STOP_CALIB = datetime(2015, 9, 16, 7, 3, 00)

CALIB_CELLS = {'a37'    :   [8.59e17, 2.00e17],
               'a53'    :   [4.15e17, 1.00e17],
               'a57'    :   [19.24e17, 3.00e17]}

PLUME_FILE = join(IMG_DIR, 
                  'EC2_1106307_1R02_2015091607110434_F01_Etna.fts')
PLUME_FILE_NEXT = join(IMG_DIR, 
                       'EC2_1106307_1R02_2015091607113241_F01_Etna.fts')
BG_FILE_ON = join(IMG_DIR, 'EC2_1106307_1R02_2015091607022602_F01_Etna.fts')
BG_FILE_OFF = join(IMG_DIR, 'EC2_1106307_1R02_2015091607022216_F02_Etna.fts')

if exists(BASE_DIR):
     
    @pytest.fixture
    def plume_img(scope="module"):
        return Img(PLUME_FILE).pyr_up(1)
    
    @pytest.fixture
    def plume_img_next(scope="module"):
        return Img(PLUME_FILE_NEXT).pyr_up(1)
    
    @pytest.fixture
    def bg_img_on(scope="module"):
        return Img(BG_FILE_ON).to_pyrlevel(0)
    
    @pytest.fixture
    def bg_img_off(scope="module"):
        return Img(BG_FILE_OFF).to_pyrlevel(0)
    
    @pytest.fixture
    def setup(scope="module"):
        cam_id = "ecII"
        
        ### Define camera (here the default ecII type is used)
        img_dir = IMG_DIR
        
        ### Load default information for Etna
        source = Source("etna")
        
        #### Provide wind direction
        wind_info= {"dir"     : 0.0,
                    "dir_err"  : 1.0}
    
        #camera location and viewing direction (altitude will be retrieved automatically)                    
        geom_cam = {"lon"           :   15.1129,
                    "lat"           :   37.73122,
                    "elev"          :   20.0,
                    "elev_err"      :   5.0,
                    "azim"          :   270.0,
                    "azim_err"      :   10.0,
                    "alt_offset"    :   15.0,
                    "focal_length"  :   25e-3} 
    
        #the camera filter setup
        filters= [Filter(type="on", acronym="F01"),
                  Filter(type="off", acronym="F02")]
        
        cam = Camera(cam_id, filter_list=filters, **geom_cam)
    
        return MeasSetup(img_dir, 
                         camera=cam,
                         source=source, 
                         wind_info=wind_info,
                         cell_info_dict=CALIB_CELLS,
                         auto_topo_access=False)
        
    @pytest.fixture(scope="module")
    def calib_dataset():
        """Initiate calibration dataset"""
        stp = setup()
        stp.start = START_CALIB
        stp.stop = STOP_CALIB
        
        return CellCalibEngine(stp)
    
    @pytest.fixture(scope="module")
    def plume_dataset():
        """Initiates measurement setup and creates dataset from that"""
        stp = setup()
        stp.start = START_PLUME
        stp.stop = STOP_PLUME
        ### Create analysis object (from BaseSetup)
        # The dataset takes care of finding all vali
        return Dataset(stp)   
    
    @pytest.fixture(scope="module")
    def aa_image_list():
        """Prepares AA image list for further analysis"""
        ds = plume_dataset()
        geom = test_find_viewdir()
    
        ### Get on and off lists and activate dark correction
        lst = ds.get_list("on")
        lst.activate_darkcorr() #same as lst.darkcorr_mode = 1
        
        off_list = ds.get_list("off")
        off_list.activate_darkcorr()
    
        # Prepare on and offband background images
        bg_on = bg_img_on() 
        bg_on.subtract_dark_image(lst.get_dark_image().to_pyrlevel(0))
        
        bg_off = bg_img_off()
        bg_off.subtract_dark_image(off_list.get_dark_image().to_pyrlevel(0))
        
        #set the background images within the lists
        lst.set_bg_img(bg_on)
        off_list.set_bg_img(bg_off)
        
        # automatically set gas free areas
        # NOTE: this corresponds to pyramid level 3 as the test data is 
        # stored in low res
        lst.bg_model.set_missing_ref_areas(lst._this_raw)
        #Now update some of the information from the automatically set sky ref 
        #areas    
        lst.bg_model.xgrad_line_startcol = 1
        lst.bg_model.xgrad_line_rownum = 2
        off_list.bg_model.xgrad_line_startcol = 1
        off_list.bg_model.xgrad_line_rownum = 2
        
        lst.bg_model.CORR_MODE = 0
        off_list.bg_model.CORR_MODE = 0

        #lst.aa_mode = True # activate AA mode 
# =============================================================================
#         
#         m = lst.bg_model
#         ax = lst.show_current()
#         ax.set_title("MODE: %d" %m.mode)
#         m.plot_tau_result()
#     
# =============================================================================
# =============================================================================
#         for mode in range(1,7):
#             lst.bg_model.CORR_MODE = mode
#             off_list.bg_model.CORR_MODE = mode
#             lst.load()
#             ax = lst.show_current()
#             ax.set_title("MODE: %d" %m.mode)
#             m.plot_tau_result()
# =============================================================================
        lst.meas_geometry = geom
        return lst
    
    @pytest.fixture
    def line(scope="module"):
        """Create an example retrieval line"""
        return LineOnImage(630, 780, 1000, 350, pyrlevel_def=0, 
                           normal_orientation="left")
    
    def test_setup():
        """Test some properties of the MeasSetup object"""
        stp = setup()
        s = stp.source
        vals_exact = [stp.save_dir == stp.base_dir,
                      stp.camera.cam_id]
        vals_approx = [sum([sum(x) for x in stp.cell_info_dict.values()]),
                    s.lon + s.lat + s.altitude]
        
        nominal_exact = [True, "ecII"]
        nominal_approx = [3.798e18, 3381.750]
        
        assert vals_exact == nominal_exact 
        assert_allclose(vals_approx, nominal_approx, rtol=1e-4)
        
    def test_dataset():
        """Test certain properties of the dataset object"""
        ds = plume_dataset()
        vals_exact = [ds.img_lists["on"].nof + ds.img_lists["off"].nof,
                    sum(ds.current_image("on").shape),
                    ds.img_lists_with_data.keys(), 
                    ds.cam_id]
        
        nominal_exact = [178, 2368, ["on", "off"], "ecII"]
        
        assert vals_exact == nominal_exact
    
    def test_find_viewdir():
        """Correct viewing direction using location of Etna SE crater
        """
        from geonum import GeoPoint
        geom = plume_dataset().meas_geometry
        # Position of SE crater in the image (x, y)
        se_crater_img_pos = [806, 736] 
        
        # Geographic position of SE crater (extracted from Google Earth)
        # The GeoPoint object (geonum library) automatically retrieves the altitude
        # using SRTM data 
        se_crater = GeoPoint(37.747757, 15.002643, altitude=3103.0)
        
        print "Prev. elev / azim: %.2f / %.2f" %(geom.cam["elev"], geom.cam["azim"])
        # The following method finds the camera viewing direction based on the
        # position of the south east crater. 
        new_elev, new_azim, _, basemap =\
        geom.find_viewing_direction(pix_x=se_crater_img_pos[0], 
                                    pix_y=se_crater_img_pos[1],
                                    pix_pos_err=100, #for uncertainty estimate
                                    geo_point=se_crater,
                                    draw_result=False,
                                    update=True) #overwrite old settings
        print "Prev. elev / azim (NEW): %.2f / %.2f" %(geom.cam["elev"], geom.cam["azim"])
        return geom
    
    def test_imglists():
        """Test some properties of the on and offband image lists"""
        ds = plume_dataset()
        on = ds._lists_intern["F01"]["F01"]
        off = ds._lists_intern["F02"]["F02"]
        
        vals_exact = [on.list_id, off.list_id]
        
        nominal_exact = ["on", "off"]
        
        assert vals_exact == nominal_exact
        
    def test_line():
        """Test some features from example retrieval line"""
        l = line()
        n1, n2 = l.normal_vector
        l1 = l.convert(1, [100, 100, 1200, 1024])
        
        #compute values to be tested
        vals = [l.length(), l.normal_theta, n1, n2, l1.length()/l.length(),
                sum(l1.roi_def)]
        #set nominal values
        nominal = [567, 310.71, -0.76, -0.65, 0.5, 1212]
        assert_almost_equal(vals, nominal, 2)
    
    def test_geometry():
        """Test important results from geometrical calculations"""
        geom = plume_dataset().meas_geometry
        res = geom.compute_all_integration_step_lengths()
        assert_almost_equal([1.9032662, 1.9032662, 10232.611],
                            [res[0].mean(), res[1].mean(), res[2].mean()],
                            3)
    def test_optflow():
        """Test optical flow calculation"""
        flow = OptflowFarneback()
        img = plume_img()
        flow.set_images(img, plume_img_next())
        flow.calc_flow()
        len_img = flow.get_flow_vector_length_img()
        angle_img = flow.get_flow_orientation_img()
        l = line().convert(img.pyrlevel)
        res = flow.local_flow_params(line=l, dir_multi_gauss=False)
        flow.plot_flow_histograms()
        nominal = [2.032, -43.881, -67.625, 18.266, 0.185, 0.085,  
                   28.07, 0.864]
        vals = [len_img.mean(), angle_img.mean(), res["_dir_mu"],
                res["_dir_sigma"], res["_len_mu_norm"], 
                res["_len_sigma_norm"], res["_del_t"], 
                res["_significance"]]
        assert_almost_equal(vals, nominal, 3)
        return flow
        
    def test_auto_cellcalib():
        """Test if automatic cell calibration works"""
        ds = calib_dataset()
        ds.find_and_assign_cells_all_filter_lists()
        keys = ["on", "off"]
        nominal = [6, 844.03541, 353.96765, 3, 3]
        mean = 0
        bg_mean = ds.bg_lists["on"].this.mean() +\
                  ds.bg_lists["off"].this.mean()
        num = 0
        for key in keys:
            for lst in ds.cell_lists[key].values():
                mean += lst.this.mean()
                num += 1
        vals = [num, mean, bg_mean, len(ds.cell_lists["on"]),
                len(ds.cell_lists["off"])]
        assert_almost_equal(nominal, vals, 5)
        
    def test_bg_model():
        """Test properties of plume background modelling
        
        Uses the PlumeBackgroundModel instance in the on-band image
        list of the test dataset object (see :func:`plume_dataset`)
        """
        l = plume_dataset().get_list("on")
        m = l.bg_model
        with pytest.raises(ValueError) as e_info:
            m.plot_sky_reference_areas(l.this)
        m.set_missing_ref_areas(l.this)
        
        return m
        #m.set_missing_ref_areas(plume_img())
        
        
    
        
if __name__=="__main__":
    import matplotlib.pyplot as plt
    plt.rcParams["font.size"] = 14
    plt.close("all")
    
    lst = aa_image_list()
    lst.tau_mode = True
    

    
    #lst.bg_model.plot_sky_reference_areas(lst.bg_model._current_imgs["plume"])
    
    
# =============================================================================
#     
#     flow = test_optflow()
#     l=line()
#     
#     ds = calib_dataset()
#     ds.find_and_assign_cells_all_filter_lists()
# =============================================================================
    #cell = calib_dataset()
# =============================================================================
#     cell.find_and_assign_cells_all_filter_lists()
#     cell.plot_cell_search_result()
#     
# =============================================================================