"""
Code to apply non-linearity correction.
"""
from __future__ import print_function

import numpy as np

import scipy.optimize
from scipy.interpolate import UnivariateSpline

import astropy.io.fits as fits

from lsst.eotest.fitsTools import fitsTableFactory, fitsWriteto


def lin_func(pars, xvals):
    """Return a line whose slope is pars[0]"""
    return pars[0]*xvals

def chi2_model(pars, xvals, yvals):
    """Return the chi2 w.r.t. the model"""
    return (yvals - lin_func(pars, xvals))/np.sqrt(yvals)

def make_profile_hist(xbin_edges, xdata, ydata, **kwargs):
    """Build a profile historgram

    Parameters
    ----------
    xbin_edges : `array`
        The bin edges
    xdata : `array`
        The x-axis data
    ydata : `array`
        The y-axis data

    Keywords
    --------
    yerrs :  `array`
        The errors on the y-axis points

    stderr : `bool`
        Set error bars to standard error instead of RMS

    Returns
    -------
    x_vals : `array`
        The x-bin centers
    y_vals : `array`
        The y-bin values
    y_errs : `array`
        The y-bin errors
    """
    yerrs = kwargs.get('yerrs', None)
    stderr = kwargs.get('stderr', False)

    nx = len(xbin_edges) - 1
    x_vals = (xbin_edges[0:-1] + xbin_edges[1:])/2.
    y_vals = np.ndarray((nx))
    y_errs = np.ndarray((nx))

    if yerrs is None:
        weights = np.ones(ydata.shape)
    else:
        weights = 1./(yerrs*yerrs)

    y_w = ydata*weights

    for i, (xmin, xmax) in enumerate(zip(xbin_edges[0:-1], xbin_edges[1:])):
        mask = (xdata >= xmin) * (xdata < xmax)
        if mask.sum() < 2:
            y_vals[i] = 0.
            y_errs[i] = -1.
            continue
        y_vals[i] = y_w[mask].sum() / weights[mask].sum()
        y_errs[i] = ydata[mask].std()
        if stderr:
            y_errs[i] /= np.sqrt(mask.sum())

    return x_vals, y_vals, y_errs


class NonlinearityCorrection:
    """Apply a non-linearity correction

    The point of this calls is to serve as a callable object that will
    linearize bias-subtracted data

    corrected_adu = nlc(amp, uncorrected_adu)

    This is implemented as a spline interpolation for each of the 16 amplifiers on a CCD
    """
    def __init__(self, prof_x, prof_y, prof_yerr):
        """C'tor

        Parameters
        ----------
        prof_x : `array`
            Array of 16 x nbins values for the x-axis of the correction function
        prof_y : `array`
            Array of 16 x nbins values for the y-axis of the correction function
        prof_yerr : `array`
            Array of 16 x nbins values for the y-axis of the correction function
        """
        self._prof_x = prof_x
        self._prof_y = prof_y
        self._prof_yerr = prof_yerr
        self._nxbins = self._prof_x.shape[1]

        self._spline_dict = {}
        for iamp in range(16):
            profile_x = self._prof_x[iamp]
            profile_y = self._prof_y[iamp]
            if self._prof_yerr is not None:
                profile_yerr = self._prof_yerr[iamp]
                mask = profile_yerr >= 0.
            else:
                mask = np.ones(profile_x.shape)
            self._spline_dict[iamp] = UnivariateSpline(profile_x[mask], profile_y[mask])

    def __getitem__(self, amp):
        """Get the function that corrects a particular amp"""
        return self._spline_dict[amp]

    def __call__(self, amp, adu):
        """Apply the non-linearity correction to a particular amp"""
        return adu*(1 + self._spline_dict[amp-1](adu))


    def write_to_fits(self, fits_file):
        """Write this object to a FITS file"""
        output = fits.HDUList()
        output.append(fits.PrimaryHDU())

        col_prof_x = fits.Column(name='prof_x', format='%iE' % self._nxbins,
                                 unit='e-', array=self._prof_x)
        col_prof_y = fits.Column(name='prof_y', format='%iE' % self._nxbins,
                                 unit='e-', array=self._prof_y)
        col_prof_yerr = fits.Column(name='prof_yerr', format='%iE' % self._nxbins,
                                    unit='e-', array=self._prof_yerr)

        fits_cols = [col_prof_x, col_prof_y, col_prof_yerr]
        hdu = fitsTableFactory(fits_cols)
        hdu.name = 'nonlin'
        output.append(hdu)

        fitsWriteto(output, fits_file, overwrite=True)


    def save_plots(self, plotfile, **kwargs):
        """Save plots showing the nonlinearity correction"""
        import matplotlib.pyplot as plt
        ymin = kwargs.get('ymin', None)
        ymax = kwargs.get('ymax', None)

        figsize = kwargs.get('figsize', (15, 10))

        fig, axs = plt.subplots(nrows=4, ncols=4, figsize=figsize)
        fig.suptitle("Nonlinearity")

        xlabel = r'Mean [ADU]'
        ylabel = r'Frac Resid [$(q - g\mu)/g\mu$]'
        for i_row in range(4):
            ax_row = axs[i_row, 0]
            ax_row.set_ylabel(ylabel)

        for i_col in range(4):
            ax_col = axs[3, i_col]
            ax_col.set_xlabel(xlabel)

        iamp = 0
        for i_row in range(4):
            for i_col in range(4):
                axes = axs[i_row, i_col]
                if ymin is not None or ymax is not None:
                    axes.set_ylim(ymin, ymax)
                mask = self._prof_yerr[iamp] >= 0.
                x_masked = self._prof_x[iamp][mask]
                xline = np.linspace(1., x_masked.max(), 1001)
                axes.errorbar(x_masked, self._prof_y[iamp][mask],
                              yerr=self._prof_yerr[iamp][mask], fmt='.')
                axes.plot(xline, self._spline_dict[iamp](xline), 'r-')
                iamp += 1
        if plotfile is None:
            fig.show()
        else:
            fig.savefig(plotfile)


    @classmethod
    def create_from_table(cls, table):
        """Create a NonlinearityCorrection object from a fits file

        Parameters
        ----------
        table : `Table`
            The table data used to build the nonlinearity correction

        Returns
        -------
        nl : `NonlinearityCorrection`
            The requested object
        """
        prof_x = table.data['prof_x']
        prof_y = table.data['prof_y']
        prof_yerr = table.data['prof_yerr']
        return cls(prof_x, prof_y, prof_yerr)

    @classmethod
    def create_from_fits_file(cls, fits_file, hdu_name='nonlin'):
        """Create a NonlinearityCorrection object from a fits file

        Parameters
        ----------
        fits_file : `str`
            The file with the data used to build the nonlinearity correction

        hdu_name : `str`
            The name of the HDU with the nonlinearity correction data

        Returns
        -------
        nl : `NonlinearityCorrection`
            The requested object
        """
        hdulist = fits.open(fits_file)
        table = hdulist[hdu_name]
        nl = cls.create_from_table(table)
        hdulist.close()
        return nl

    @classmethod
    def create_from_det_response(cls, detresp, fit_range=(0., 9e4), nprofile_bins=10):
        """Create a NonlinearityCorrection object from a fits file

        Parameters
        ----------
        detresp : `DetectorResponse`
            An object with the detector response calculated from flat-pair files

        fit_range : `tuple`
            The range over which to define the non-linearity

        nprofile_bins : `int`
             The number of bins to use in the profile

        Returns
        -------
        nl : `NonlinearityCorrection`
            The requested object
        """
        xbins = np.linspace(fit_range[0], fit_range[1], nprofile_bins+1)

        prof_x = np.ndarray((16, nprofile_bins))
        prof_y = np.ndarray((16, nprofile_bins))
        prof_yerr = np.ndarray((16, nprofile_bins))

        for idx, amp in enumerate(detresp.Ne):
            xdata = detresp.Ne[amp]
            mask = (fit_range[0] < xdata) * (fit_range[1] > xdata)
            xdata_fit = detresp.Ne[amp][mask]
            ydata_fit = detresp.flux[mask]
            mean_slope = (ydata_fit/xdata_fit).mean()
            pars = (mean_slope,)
            results = scipy.optimize.leastsq(chi2_model, pars,
                                             full_output=1,
                                             args=(xdata_fit, ydata_fit))
            model_yvals = lin_func(results[0], xdata)
            frac_resid = (detresp.flux - model_yvals)/model_yvals
            frac_resid_err = 1./xdata

            prof_x[idx], prof_y[idx], prof_yerr[idx] = make_profile_hist(xbins, detresp.Ne[amp], frac_resid,
                                                                         y_errs=frac_resid_err, stderr=True)

        return cls(prof_x, prof_y, prof_yerr)