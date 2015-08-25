"""
@brief Pixel response non-uniformity.

@author J. Chiang <jchiang@slac.stanford.edu>
"""
import os
from collections import OrderedDict
import numpy as np
import pyfits
from lsst.eotest.pyfitsTools import pyfitsTableFactory, pyfitsWriteto
import lsst.eotest.image_utils as imutils
from prnu import prnu
import lsst.afw.image as afwImage
import lsst.pex.config as pexConfig
import lsst.pipe.base as pipeBase

class PrnuConfig(pexConfig.Config):
    """Configuration for pixel response non-uniformity task"""
    output_dir = pexConfig.Field("Output directory", str, default=".")
    eotest_results_file = pexConfig.Field("EO test results filename", 
                                          str, default=None)
    verbose = pexConfig.Field("Turn verbosity on", bool, default=True)

class PrnuTask(pipeBase.Task):
    """Task for computing pixel response non-uniformity"""
    ConfigClass = PrnuConfig
    _DefaultName = "PrnuTask"

    @pipeBase.timeMethod
    def run(self, sensor_id, prnu_files, mask_files, gains, correction_image):
        results = OrderedDict()
        line = "wavelength (nm)   pixel_stdev   pixel_mean"
        if self.config.verbose:
            self.log.info(line)
        wl_index = {}
        for infile in prnu_files:
            md = imutils.Metadata(infile, 1)
            wl = int(np.round(md.get('MONOWL')))
            wl_index[wl] = infile
        for wl in (350, 450, 500, 620, 750, 870, 1000):
            if wl_index.has_key(wl):
                self.log.info("Processing: wl = %i nm, %s" % (wl, wl_index[wl]))
                pix_stdev, pix_mean = prnu(wl_index[wl], mask_files, gains,
                                           correction_image=correction_image)
                results[wl] = pix_stdev, pix_mean
                line = "%6.1f  %12.4e  %12.4e" % (wl, pix_stdev, pix_mean)
            else:
                # Enter sentinel values for pixel stats for
                # wavelengths that do not have the corresponding
                # exposure
                line = "%6.1f  %12s  %12s" % (wl, '    ...     ', 
                                              '    ...     ')
                results[wl] = -1, -1
            if self.config.verbose:
                self.log.info(line)
        results_file = self.config.eotest_results_file
        if results_file is None:
            outfile = os.path.join(self.config.output_dir,
                                   '%s_eotest_results.fits' % sensor_id)
        self.write(results, outfile)
        return results
    @pipeBase.timeMethod
    def write(self, results, outfile, clobber=True):
        colnames = ['WAVELENGTH', 'STDEV', 'MEAN']
        formats = 'IEE'
        my_types = dict((("I", np.int), ("E", np.float)))
        columns = [np.zeros(len(results), dtype=my_types[fmt])
                            for fmt in formats]
        units = ['nm', 'rms e-', 'e-']
        hdu = pyfitsTableFactory([pyfits.Column(name=colnames[i],
                                                format=formats[i],
                                                unit=units[i],
                                                array=columns[i])
                                  for i in range(len(colnames))])
        hdu.name = 'PRNU_RESULTS'
        for i, wl in enumerate(results.keys()):
            hdu.data.field('WAVELENGTH')[i] = wl
            hdu.data.field('STDEV')[i] = results[wl][0]
            hdu.data.field('MEAN')[i] =results[wl][1]
        if os.path.isfile(outfile):
            output = pyfits.open(outfile)
        else:
            output = pyfits.HDUList()
            output.append(pyfits.PrimaryHDU())
        try:
            output[hdu.name] = hdu
        except KeyError:
            output.append(hdu)
        pyfitsWriteto(output, outfile, clobber=clobber)
