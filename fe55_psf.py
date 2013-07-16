"""
@brief Fit 2D Gaussian to Fe55 footprints to determine the Gaussian
width of the charge dispersed signals.  For each footprint, also
compute the probability of the chi-square fit.

@author J. Chiang <jchiang@slac.stanford.edu>
"""
import numpy as np
import pyfits
import scipy.optimize
from scipy.special import erf, gammaincc

import lsst.afw.detection as afwDetect
import lsst.afw.image as afwImage
import lsst.afw.math as afwMath

import image_utils as imutils
from MaskedCCD import MaskedCCD

_sqrt2 = np.sqrt(2)

def pixel_integral(x, y, x0, y0, sigma):
    """
    Integrate 2D Gaussian centered at (x0, y0) with width sigma over a
    square pixel at (x, y) with unit width.
    """
    x1, x2 = x - 0.5, x + 0.5
    y1, y2 = y - 0.5, y + 0.5

    Fx = 0.5*(erf((x2 - x0)/_sqrt2/sigma) - erf((x1 - x0)/_sqrt2/sigma))
    Fy = 0.5*(erf((y2 - y0)/_sqrt2/sigma) - erf((y1 - y0)/_sqrt2/sigma))
    
    return Fx*Fy

def psf_func(pos, *args):
    """
    For a pixel location or list of pixel locations, pos, compute the
    DN for a 2D Gaussian with parameters args.
    """
    x0, y0, sigma, DN = args
    if type(pos) == type([]):
        return DN*np.array([pixel_integral(x[0], x[1], x0, y0, sigma) 
                            for x in pos])
    return DN*pixel_integral(x[0], x[1], x0, y0, sigma)

def chisq(pos, dn, args):
    "The chi-square of the fit of the data to psf_func."
    return sum((psf_func(pos, *tuple(args)) - np.array(dn))**2)

class PsfGaussFit(object):
    def __init__(self, nsig=3, min_npix=5):
        """
        nsig is the threshold in number of clipped stdev above median.
        min_npix is the minimum number of pixels to be used in the
        4-parameter fit.
        """
        self.nsig = nsig
        self.min_npix = min_npix
        self.sigma, self.dn, self.chiprob = [], [], []
        self.output = pyfits.HDUList()
        self.output.append(pyfits.PrimaryHDU())
    def process_image(self, image, amp, sigma0=0.36, dn0=1590./5.):
        """
        Process a segment and accumulate the results in self.sigma,
        and self.chiprob. The dn0 and sigma0 parameters are the
        starting values used for each fit.
        """
        image -= imutils.bias_image(image)
        try:
            imarr = image.getArray()
        except AttributeError:
            imarr = image.getImage().getArray()

        flags = afwMath.MEDIAN | afwMath.STDEVCLIP
        statistics = afwMath.makeStatistics(image, flags) 
        median = statistics.getValue(afwMath.MEDIAN)
        stdev = statistics.getValue(afwMath.STDEVCLIP)

        threshold = afwDetect.Threshold(median + self.nsig*stdev)
        fpset = afwDetect.FootprintSet(image, threshold)

        x0, y0 = [], []
        sigma, dn, chiprob = [], [], []
        for fp in fpset.getFootprints():
            if fp.getNpix() < self.min_npix:
                continue
            spans = fp.getSpans()
            positions = []
            zvals = []
            peak = [pk for pk in fp.getPeaks()][0]
            p0 = (pk.getIx(), pk.getIy(), sigma0, dn0)
            for span in spans:
                y = span.getY()
                for x in range(span.getX0(), span.getX1() + 1):
                    positions.append((x, y))
                    zvals.append(imarr[y][x])
            try:
                pars, _ = scipy.optimize.curve_fit(psf_func, positions, 
                                                   zvals, p0=p0)
                x0.append(pars[0])
                y0.append(pars[1])
                sigma.append(pars[2])
                dn.append(pars[3])
                chi2 = chisq(positions, zvals, pars)
                dof = fp.getNpix() - 4
                chiprob.append(gammaincc(dof/2., chi2/2.))
            except RuntimeError:
                pass
        self._save_ext_data(amp, x0, y0, sigma, dn, chiprob)
        self.sigma.extend(sigma)
        self.dn.extend(dn)
        self.chiprob.extend(chiprob)
    def _save_ext_data(self, amp, x0, y0, sigma, dn, chiprob):
        """
        Fill a FITS extension with results from source detection and
        Gaussian fitting.
        """
        colnames = ['XPOS', 'YPOS', 'SIGMA', 'DN', 'CHIPROB']
        columns = [np.array(x0), np.array(y0), np.array(sigma),
                   np.array(dn), np.array(chiprob)]
        formats = ['E']*len(columns)
        units = ['pixel', 'pixel', 'pixel', 'ADU', 'None']
        fits_cols = lambda coldata : [pyfits.Column(name=colname,
                                                    format=format,
                                                    unit=unit,
                                                    array=column)
                                      for colname, format, unit, column
                                      in coldata]
        self.output.append(pyfits.new_table(fits_cols(zip(colnames, formats,
                                                          units, columns))))
        self.output[-1].name = 'Segment%s' % imutils.channelIds[amp]
    def results(self, min_prob=0.1):
        """
        Return sigma, dn, chiprob for chiprob > min_prob.
        """
        sigma = np.array(self.sigma, dtype=np.float)
        dn = np.array(self.dn, dtype=np.float)
        chiprob = np.array(self.chiprob, dtype=np.float)
        indx = np.where(chiprob > min_prob)
        return sigma[indx], dn[indx], chiprob[indx]
    def write_results(self, outfile='fe55_psf_params.fits'):
        self.output.writeto(outfile, clobber=True)

if __name__ == '__main__':
    import pylab_plotter as plot
    plot.pylab.ion()

    #infile = 'simulation/sensorData/000-00/fe55/debug/000-00_fe55_fe55_00_debug.fits'
    infile = 'fe55_0060s_000.fits'
    outfile = '000-00_fe55_psf.fits'

    ccd = MaskedCCD(infile)

    fitter = PsfGaussFit()
    for amp in imutils.allAmps:
        print 'processing amp:', amp
        fitter.process_image(ccd[amp], amp)
    fitter.write_results(outfile)

    sigma, dn, chiprob = fitter.results()

    flags = afwMath.MEDIAN | afwMath.STDEVCLIP

    stats = afwMath.makeStatistics(sigma, flags)
    median = stats.getValue(afwMath.MEDIAN)
    stdev = stats.getValue(afwMath.STDEVCLIP)
    plot.histogram(sigma, xname='Fitted sigma values',
                   xrange=(median-3*stdev, median+3*stdev))

    plot.histogram(dn, xname='Fitted DN values', xrange=(250, 450),
                   yrange=(0, 100))

    plot.xyplot(chiprob, sigma, xname='chi-square prob.', yname='sigma')
