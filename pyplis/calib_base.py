# -*- coding: utf-8 -*-
#
# Pyplis is a Python library for the analysis of UV SO2 camera data
# Copyright (C) 2017 Jonas Gliß (jonasgliss@gmail.com)
#
# This program is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License a
# published by the Free Software Foundation, either version 3 of
# the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""
Pyplis module containing the :class:`CalibData` which is the base class 
for storing calibration data, fitting calibration curves, and corresponding
I/O routines (e.g storage as FITS or text file). 
"""
from numpy import (min, asarray, zeros, linspace, ones, float64, isnan, 
                   ndarray, argmax, inf)
from inspect import getargspec
from scipy.optimize import curve_fit

from datetime import datetime 
from pandas import Series
from astropy.io import fits
from os.path import join, exists, isdir, abspath, basename, dirname
from warnings import warn

from matplotlib.pyplot import subplots

from .glob import SPECIES_ID, CALIB_ID_STRINGS
from .processing import ImgStack
from .helpers import exponent, isnum
        
from .model_functions import CalibFuns
from .image import Img
from .setupclasses import Camera


class CalibData(object):
    """Base class representing calibration data and optimisation parameters
    
    The default calibration curve is a polynomial of first order. Calibration
    data is represneted by two arrays ``cd_vec`` and ``tau_vec`` and
    optionally, a vector containing errors in the column densities 
    ``cd_vec_err`` (note that errors in the optical densities are not 
    supported). 
    Furthermore, an array of ``time_stamps`` can be provided and
    If you want to use a custom calibration function you can 
    provide the function using :param:`calib_fun`. 
    
    
    Parameters
    ----------
    tau_vec : ndarray
        tau data vector for calibration data
    cd_vec : ndarray
        DOAS-CD data vector for calibration data
    cd_vec_err : ndarray
        Fit errors of DOAS-CDs
    time_stamps : ndarray
        array with datetime objects containing time stamps 
        (e.g. start acquisition) of calibration data
    calib_fun : function
        optimisation function used for fitting of calibration data
    calib_coeffs : ;obj:`list`, optional
        optimisation parameters for calibration curve. 
    senscorr_mask : :obj:`ndarray`or :obj:`Img`, optional
        sensitivity correction mask that was normalised relative to the 
        pixel position where the calibration data was retrieved (i.e. 
        position of DOAS FOV in case of DOAS calibration data, or image pixel 
        position, where cell calibration data was retrieved)
    calib_id : str
        calibration ID (e.g. "aa", "tau_on", "tau_off")
    camera : Camera
        camera object (not necessarily required). A camera can be assigned 
        in order to convert the FOV extend from pixel coordinates into 
        decimal degrees
        
    """
    def __init__(self, tau_vec=[], cd_vec=[], cd_vec_err=[], time_stamps=[], 
                 calib_fun=None, calib_coeffs=[], senscorr_mask=None, 
                 polyorder=1, calib_id="", camera=None):
        # type of calibration performed (e.g. "doas", "cell")
        self.type = "base"
        # ID specifying image OD type (e.g. "on", "off", "aa")
        self.calib_id = calib_id
        #tau data vector within FOV
        self.tau_vec = asarray(tau_vec).astype(float64)
        
        self._calib_funs = CalibFuns()
        num = len(tau_vec)
        #CDs data vector
        if not len(cd_vec) == len(tau_vec):
            raise ValueError("Length mismatch between tau_vec and cd_vec")
            
        self.cd_vec = asarray(cd_vec).astype(float64)
        if not len(cd_vec_err) == len(cd_vec):
            cd_vec_err = zeros(len(cd_vec))
            
        self.cd_vec_err = asarray(cd_vec_err).astype(float64)
        
        try:
            num = len(tau_vec)
            if not len(time_stamps) == num:
                raise AttributeError
            elif not isinstance(time_stamps[0], datetime):
                raise ValueError
        except:
            time_stamps = asarray([datetime(1900,1,1)] * num)
        
        self.time_stamps = time_stamps
        
        
        if camera is None:
            camera = Camera()
        self.camera = camera
        
        if senscorr_mask is None:
            try:
                senscorr_mask = ones((self.camera.pixnum_y,
                                      self.camera.pixnum_x))
            except:
                warn("Could not retrieve image dimensions from camera "
                     "(probably since no camera was provided on input). "
                     "Initiating attribute senscorr_mask with ones and "
                     "shape=(10, 10)")
                senscorr_mask = ones((10, 10))
                
        self.senscorr_mask = senscorr_mask
        
        self.fit_weighted = True
        # irrelevant if custom fit function is provided
        self.poly_through_origin = False
        
        self._calib_fun = None
        self.fit_residual = None
        self._calib_coeffs = None
        self._cov = None
        
        self._polyorder = None

        try:
            self.calib_fun = calib_fun
        except:
            pass
        try:
            self.calib_coeffs = calib_coeffs
        except:
            pass
        self.polyorder = polyorder
    
    def num_optargs_fun(self, fun):
        """Returns number of optimisation args of a function"""
        return len(getargspec(fun).args) - 1
    
    @property
    def calib_coeffs(self):
        """List containing calibration coefficients for :attr:`calib_fun`"""
        return self._calib_coeffs
    
    @calib_coeffs.setter
    def calib_coeffs(self, val):
        try:
            iter(val)
        except:
            raise TypeError("Input is not iterable, need list, tuple or "
                            "similar, containing optimisation coefficients")
        req_num_args = self.num_optargs_fun(self.calib_fun)
        if not len(val) == req_num_args:
            raise AttributeError("Number of provided coefficients does not "
                                 "match the number of optimisation params "
                                 "in current optimisation function. "
                                 "Please check and update class attribute "
                                 "calib_fun first...")
        if len(self._calib_coeffs) > 0:
            warn("Setting calibration coefficients manually. This may introduce "
                 "analysis errors. It is recommended to use the method "
                 "fit_calib_data instead")
        self._calib_coeffs = val

    @property 
    def calib_fun(self):
        """Mathematical function used for retrieval of calibration curve
        
        Note
        ----
        The function can be defined on class initiation and may be updated 
        using the setter method. If not explicitely specified, a polynomial is
        used with order :attr:`polyorder`.
        """
        if not callable(self._calib_fun):
            return self._calib_funs.get_poly(self.polyorder, 
                                             self.poly_through_origin)
        return self._calib_fun
    
    @calib_fun.setter
    def calib_fun(self, val):
        if not callable(val):
            raise ValueError("Need a callable object (e.g. lambda function)")
        args = getargspec(val).args
        print("Setting optimisation function in CalibData class. "
              "Argspec: %s" %args)
        self._calib_fun = val
        
    @property
    def start(self):
        """Start time of calibration data (datetime)"""
        try:
            return self.time_stamps[0]
        except:
            raise ValueError("Start time could not be accessed")
    
    @property
    def stop(self):
        """Stop time of calibration data (datetime)"""
        try:
            return self.time_stamps[-1]
        except:
            raise ValueError("Start time could not be accessed")
        
    @property
    def polyorder(self):
        """Current order of fit polynomial"""
        return self._polyorder
    
    @polyorder.setter
    def polyorder(self, val):
        allowed = self._calib_funs.available_poly_orders(
                    self.poly_through_origin)
        if not val in allowed:
            raise ValueError("Invalid value for polyorder: %.1f. "
                             "Choose from %s" %(val, allowed))
        self._polyorder = val
            
    @property
    def cov(self):
        """Covariance matriy of calibration polynomial"""
        if not isinstance(self._cov, ndarray):
            self.fit_calib_data()
        return self._cov
    
    @cov.setter
    def cov(self, value):
        raise IOError("Covariance matrix of calibration data cannot "
                      "be set manually, please call function "
                      "fit_calib_data")
        
    @property
    def calib_id_str(self):
        """String for calibration ID"""
        idx=0
        try:
            if self.calib_id.split("_")[1].lower() == "aa":
                idx=1
            try:
                return CALIB_ID_STRINGS[self.calib_id.split("_")[idx]]
            except:
                return self.calib_id.split("_")[idx]
        except:
            return ""
# =============================================================================
#     @property
#     def slope(self):
#         """Slope of current calib curve"""
#         if self.polyorder > 1:
#             warn("Order of calibration polynomial > 1: use value of slope with "
#                  "care (i.e. also check curvature coefficients of polynomial")
#              
#         return self.coeffs[-2]
#         
#     @property
#     def slope_err(self):
#         """Slope error of current calib curve"""
#         if self.polyorder > 1:
#             warn("Order of calibration polynomial > 1: use slope error with "
#                  "care")
#         return sqrt(self.cov[-2][-2])
# =============================================================================
    
    @property
    def y_offset(self):
        """Y-axis offset of calib curve"""
        return self.calib_fun(0, *self.calib_coeffs)
    
# =============================================================================
#     @property
#     def y_offset_err(self):
#         """Error of y axis offset of calib curve"""
#         return sqrt(self.cov[-1][-1])
# =============================================================================
        
    @property
    def cd_tseries(self):
        """Pandas Series object of doas data"""
        return Series(self.cd_vec, self.time_stamps)
    
    @property
    def tau_tseries(self):
        """Pandas Series object of tau data"""
        return Series(self.tau_vec, self.time_stamps)
    
    @property
    def tau_range(self):
        """Range of tau values extended by 5%
        
        Returns
        -------
        tuple
            2-element tuple, containing
            
            - float, tau_min: lower end of tau range
            - float, tau_max: upper end of tau range
        """
        tau = self.tau_vec
        taumin, taumax = tau.min(), tau.max()
        if taumin > 0:
            taumin = 0
        add = (taumax - taumin) * 0.05
        return taumin - add, taumax + add
    
    @property
    def cd_range(self):
        """Range of DOAS cd values extended by 5%"""
        cds = self.cd_vec
        cdmin, cdmax = cds.min(), cds.max()
        if cdmin > 0:
            cdmin = 0
        add = (cdmax - cdmin) * 0.05
        return (cdmin - add, cdmax + add)

# =============================================================================
#     @property
#     def _poly_str(self):
#         """Return custom string representation of polynomial"""
#         exp = exponent(self.poly.coeffs[0])
#         p = poly1d(round(self.poly / 10**(exp - 2))/10**2)
#         s = "(%s)E%+d" %(p, exp)
#         return s.replace("x", r"$\tau$")
# =============================================================================
        
    def has_calib_data(self):
        """Checks if calibration data is available"""
        if not all([len(x) > 0 for x in [self.cd_vec, self.tau_vec]]):
            return False
        if not len(self.tau_vec) == len(self.cd_vec):
            return False
        return True
    
    def _check_bounds(self, fun, bounds):
        """Checks fit boundaries and sets inits default if necessary"""
        sd = False
        nargs = self.num_optargs_fun(fun)
        try:
            if bounds is None:
                sd = True
            elif not len(bounds)==2:
                sd = True
            elif not (len(x)==nargs for x in bounds):
                sd = True 
        except:
            sd=True
        if sd:
            print("Input bounds invalid, initiating default") 
            bounds = (-ones(nargs)*inf, ones(nargs)*inf) 
        return bounds
    
    def fit_calib_data(self, calib_fun=None, guess=None,
                       polyorder=None, weighted=True, 
                       weights_how="abs", through_origin=False,
                       param_bounds=None, normalise_cds=False,
                       plot=False):
        """Fit calibration polynomial to current data
        
        The calibration data is fitted using a least squares optimisation. 
        Be careful with cusomised optimisation functions that are not linear
        in all their optimisation parameters (especially, with using input
        argument :arg:`normalise_cds`).
        
        Parameters
        ----------
        calib_fun : :obj:`function`, optional
            if specified, the current calibration function is updated
        guess : :obj:`list`, optional
            initial guess for optimisation (is only considerd)
        polyorder : :obj:`int`, optional
            if specified, the current polyorder is updated (only relevant
            for polynomial optimisation functions, i.e. if no custom 
            calibration function has been provided)
        weighted : bool
            performs weighted fit based on DOAS errors in ``cd_vec_err``
            (if available), defaults to True
        weights_how : str
            use "rel" if relative errors are supposed to be used (i.e.
            w=CD_sigma / CD) or "abs" if absolute error is supposed to be 
            used (i.e. w=CD_sigma).
        through_origin : bool
            only relevant for polynomial fits (i.e. if no custom fit 
            function has been provided). If True, the polynomial fit is forced 
            to cross the coordinate origin
        param_bounds : tuple
            2-element tuple containing two lists (or tuples) specifying lower
            (param_borders[0]) and upper (param_borders[1]) borders for the 
            fit parameters. If unspecified (None), the borders will 
            automatically be set to -/+ infinity 
        normalise_cds : bool
            if True, the CD vector is normalised by its exponential magnitude
            before applying the fit. 
        plot : bool
            If True, the calibration curve and the polynomial are plotted
        
        Returns
        -------
        list
            list containing optimised parameters
        """
        if not weights_how in ["rel", "abs"]:
            raise IOError("Invalid input for parameter weights_how:"
                          "Use rel for relative errors or abs for absolute"
                          "errors for calculation of weights")
        # is used in method calib_fun and only considered if a polynomial is 
        # fitted (i.e. not considered if custom calibration function is 
        # specified
        self.poly_through_origin = through_origin
        if not self.has_calib_data():
            raise ValueError("Calibration data is not available")
        if isnum(polyorder):
            self.polyorder = polyorder
        try:
            self.calib_fun = calib_fun
        except:
            pass
        fun = self.calib_fun
        if sum(isnan(self.tau_vec)) + sum(isnan(self.cd_vec)) > 0:
            raise ValueError("Encountered nans in data")
        
        exp = exponent(self.cd_vec.max())
        yerr = ones(len(self.cd_vec))
        yerr_abs = True
        if weighted:
            if not len(self.cd_vec) == len(self.cd_vec_err):
                warn("Could not perform weighted calibration fit: "
                     "Length mismatch between CD data vector "
                     "and corresponding error vector")
            elif sum(self.cd_vec_err) == 0:
                warn("Could not perform weighted calibration fit: "
                     "Values of DOAS fit errors are 0. Do you have pydoas "
                     "installed?")
            else:
                try:
                    if weights_how == "abs":
                        yerr = (self.cd_vec_err / 10**exp if normalise_cds
                                else self.cd_vec_err)
                    else:
                        yerr = self.cd_vec_err / self.cd_vec
                        yerr_abs = False
                    #ws = ws / max(ws)
                except:
                    warn("Failed to calculate weights")
        tau_vals = self.tau_vec
        if normalise_cds and callable(self._calib_fun):
            raise ValueError("Cannot use option normalise_cds with custom "
                             "fit functions (only works with polynomials")
                             
        cds = self.cd_vec / 10**exp if normalise_cds else self.cd_vec 
    
        numargs = self.num_optargs_fun(fun)
        
        if guess is not None:
            if not len(guess) == numargs:
                raise ValueError("Number of entries in initial guess does "
                                 "not match the number of optimisation args "
                                 "of the current fit function")
        elif not callable(self._calib_fun): #calibration function is poly
            guess = ones(numargs)
            idx = argmax(cds) 
            slope = cds[idx] / tau_vals[idx]
            if through_origin:
                guess[-1] = slope 
            else:
                guess[-1] = min(cds)
                guess[-2] = slope 
            
        bounds = self._check_bounds(fun, param_bounds)
            
        popt, cov = curve_fit(f=fun, 
                              xdata=tau_vals.astype(float64),
                              ydata=cds.astype(float64),
                              p0=guess,
                              bounds=bounds,
                              sigma=yerr.astype(float64),
                              absolute_sigma=yerr_abs)
    
        
        self._calib_coeffs = popt * 10**exp if normalise_cds else popt
        self._cov = cov * 10**(2*exp) if normalise_cds else cov
        
        self.residual = (self.calib_fun(self.tau_vec, *self.calib_coeffs) 
                        - self.cd_vec)
        self.residual_std = self.residual.std()
# =============================================================================
#         if through_origin:
#             num = len(tau_vals)
#             tau_vals = concatenate([tau_vals, zeros(num)])
#             cds = concatenate([cds, zeros(num)])
#             ws = concatenate([ws, ones(num)])
#         
# =============================================================================
# =============================================================================
#         coeffs, cov = polyfit(tau_vals, cds, 
#                               polyorder, w=ws, cov=True)
# =============================================================================
        #self.polyorder = polyorder
        #return (fun, coeffs, cov, tau_vals, cds, yerr, yerr_abs)
# =============================================================================
#         self.poly = poly1d(coeffs * 10**exp)
#         self._cov = cov * 10**(2*exp)
# =============================================================================
        if plot:
            self.plot()
        return self.calib_coeffs
    
    def _prep_fits_save(self):
        """Prepare FITS HDU list for storing calibration data
        
        Returns
        -------
        HDUList
            hdu list containing sensitivity correction mask and table with
            calib data
        """
        prim_hdu = fits.PrimaryHDU()
        prim_hdu.header["type"] = self.type
        prim_hdu.header["calib_id"] = self.calib_id
        prim_hdu.data = self.senscorr_mask
        
        if not len(self.cd_vec) == len(self.tau_vec):
            raise ValueError("Could not save calibration data, mismatch in "
                " lengths of data arrays")
        if not len(self.time_stamps) == len(self.cd_vec):
            self.time_stamps = asarray([datetime(1900,1,1)]*
                                        len(self.cd_vec))
        
        tstamps = [x.strftime("%Y%m%d%H%M%S%f") for x in self.time_stamps]
        col1 = fits.Column(name="time_stamps", format="25A", array=tstamps)
        col2 = fits.Column(name="tau_vec", format="D", array=self.tau_vec)
        col3 = fits.Column(name="cd_vec", format="D", array=self.cd_vec)
        if not len(self.cd_vec_err) == len(self.cd_vec):
            self.cd_vec_err = zeros(len(self.cd_vec))
        col4 = fits.Column(name="cd_vec_err", format="D", array=self.cd_vec_err)
        
                                                        
        cols = fits.ColDefs([col1, col2, col3, col4])
        arrays = fits.BinTableHDU.from_columns(cols)
        return fits.HDUList([prim_hdu, arrays])
        
        
    def _prep_fits_savepath(self, save_dir=None, save_name=None):
        save_dir = abspath(save_dir) 
        if not isdir(save_dir): #save_dir is a file path
            save_name = basename(save_dir)
            save_dir = dirname(save_dir)
        if save_name is None:
            save_name = ("calib_type_%s_id_%s_%s_%s_%s.fts" 
                         %(self.type, self.calib_id, 
                           self.start.strftime("%Y%m%d"),
                           self.start.strftime("%H%M"), 
                           self.stop.strftime("%H%M")))
        else:
            save_name = save_name.split(".")[0] + ".fts"
            
        return join(save_dir, save_name)
    
    def save_as_fits(self, save_dir=None, save_name=None, 
                     overwrite_existing=True):
        """Save calibration data as FITS file
        
        Save all relevant information in an HDU list as FITS. The first HDU
        (:class:`PrimaryHDU`) contains the sensitivity correction mask 
        (:attr:`senscorr_mask`) and the second HDU is of type
        :class:`BinTableHDU` and contains the calibration data, which contains
        the following 4 columns in the specified order:
            
            1. :attr:`time_stamps` (as strings, format: %Y%m%d%H%M%S%f)
            2. :attr:`tau_vec`
            3. :attr:`cd_vec`
            4. :attr:`cd_vec_err`
            
        Parameters
        ----------
        save_dir : str
            save directory, if None, the current working directory is used
        save_name : str
            filename of the FITS file (if None, use pyplis default naming)
        overwrite_existing : bool
            if True, an existing calibration file with the same name will
            be overwritten
        """
        hdulist = self._prep_fits_save()
        #returns abspath of current wkdir if None
        
        hdulist.writeto(self._prep_fits_savepath(save_dir,save_name), 
                        clobber=overwrite_existing)
    
    def to_csv(self):
        """Store calibration data as tab delimited text file"""
        raise NotImplementedError("Coming soon...")
        
    def load_from_fits(self, file_path):
        """Load stack object (fits)
        
        Parameters
        ----------
        file_path : str
            file path of calibration data
            
        Returns
        -------
        HDUList 
            opened HDU object (e.g. to access potential further data in a 
            function that is calling this method)
        """
        if not exists(file_path):
            raise IOError("CalibData could not be loaded, "
                          "path does not exist")
        hdu = fits.open(file_path)
        self.senscorr_mask = hdu[0].data
        self.calib_id = hdu[0].header["calib_id"]
        self.type = hdu[0].header["type"]
        ctable=hdu[1]
        try:
            times = ctable.data["time_stamps"].byteswap().newbyteorder()
            self.time_stamps = [datetime.strptime(x, "%Y%m%d%H%M%S%f")
                                for x in times]
        except:
            warn("Failed to import vector containing calib time stamps from FITS")
        try:
            self.tau_vec = ctable.data["tau_vec"].byteswap().newbyteorder()
        except:
            warn("Failed to import calibration tau vector from FITS")
        try:
            self.cd_vec = ctable.data["cd_vec"].byteswap().newbyteorder()
        except:
            warn("Failed to import CD vector from FITS")
        try:
            self.cd_vec_err = ctable.data["cd_vec_err"].byteswap().newbyteorder()
        except:
            warn("Failed to import CD uncertainty vector from FITS")
        return hdu
        
        
    def plot(self, add_label_str="", ax=None, **kwargs):
        """Plot calibration data and fit result
        
        Parameters
        ----------
        add_label_str : str
            additional string added to label of plots for legend
        ax : 
            matplotlib axes object, if None, a new one is created
        """
        if not "color" in kwargs:
            kwargs["color"] = "b"
            
        if ax is None:
            fig, ax = subplots(1,1, figsize=(10,8))
        
        taumin, taumax = self.tau_range
        x = linspace(taumin, taumax, 100)
        
        cds = self.cd_vec
        cds_calib = self.calib_fun(x, *self.calib_coeffs)
                
        ax.plot(self.tau_vec, cds, ls="", marker=".",
                label="Data %s" %add_label_str, **kwargs)
        try:
            ax.errorbar(self.tau_vec, cds, yerr=self.cd_vec_err, 
                        marker="None", ls=" ", c="#b3b3b3")
        except:
            warn("No CD errors available")
        try:
            ax.plot(x, cds_calib, ls="-", marker="",
                    label="Fit result", **kwargs)
                    
        except TypeError:
            print "Calibration poly probably not fitted"
        
        ax.set_title("Calibration data, ID: %s" %self.calib_id_str)
        ax.set_ylabel(r"$S_{%s}$ [cm$^{-2}$]" %SPECIES_ID)
        ax.set_xlabel(r"$\tau_{%s}$" %self.calib_id_str)
        ax.grid()
        ax.legend(loc='best', fancybox=True, framealpha=0.7)
        return ax
    
    def plot_calib_fun(self, add_label_str="", shift_yoffset=False, ax=None, 
                  **kwargs):
        """Plot calibration fit result
        
        Parameters
        ----------
        add_label_str : str
            additional string added to label of plots for legend
        shift_yoffset : bool
            if True, the data is plotted without y-offset
        ax : 
            matplotlib axes object, if None, a new one is created
        """
        if not "color" in kwargs:
            kwargs["color"] = "b"
            
        if ax is None:
            fig, ax = subplots(1,1, figsize=(10,8))
        
        taumin, taumax = self.tau_range
        x = linspace(taumin, taumax, 100)
    
        cds_poly = self.calib_fun(x, *self.calib_coeffs)
        if shift_yoffset:
            try:
                cds_poly -= self.y_offset
            except:
                warn("Failed to subtract y offset")
                
        try:
            ax.plot(x, cds_poly, ls="-", marker="",
                    label="Fit result %s" %add_label_str, **kwargs)
                    
        except TypeError:
            print "Calibration poly probably not fitted"
        
        ax.grid()
        ax.legend(loc='best', fancybox=True, framealpha=0.7)
        return ax
    
    def err(self):
        """Returns standard deviation of fit residual"""
        return self.residual_std
     
    def calibrate(self, value):
        """Apply calibration to input
        
        Parameters
        ----------
        value 
            optical density (can be float, n-dimensional numpy array,
            :class:`Img`, :class:`ImgStack`)
        
        Returns
        -------
            calibrated input
        """
        # make sure calibration data and fit result are available
        try:
            self.calib_fun(0, *self.calib_coeffs)
        except:
            self.fit_calib_data()
            
        if isinstance(value, Img):
            vals = (self.calib_fun(value.img, *self.calib_coeffs) 
                    -self.y_offset)
            value.img = vals
            value.edit_log["gascalib"] = True
            return value
        elif isinstance(value, ImgStack):
            vals = (self.calib_fun(value.stack, *self.calib_coeffs) 
                    -self.y_offset)
            value.stack = vals
            value.img_prep["gascalib"] = True
            return value
        return (self.calib_fun(value, *self.calib_coeffs) 
                - self.y_offset)
        
    def __call__(self, value, **kwargs):
        """Define call function to apply calibration
        
        Parameters
        ----------
        value 
            optical density, can be float or n-dimensional numpy array
        :param float value: tau or AA value
        :return: corresponding column density
        """
        return self.calibrate(value)