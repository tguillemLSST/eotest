"""
@brief Source identification and characterization of
spot images.
"""
from __future__ import print_function
from __future__ import absolute_import
import os
import numpy as np
from astropy.io import fits

import lsst.eotest.image_utils as imutils
import lsst.afw.image as afwImage
import lsst.pex.config as pexConfig
import lsst.pipe.base as pipeBase
from lsst.pipe.tasks.characterizeImage import CharacterizeImageTask, CharacterizeImageConfig
try:
    import lsst.meas.extensions.shapeHSM
except ModuleNotFoundError:
    print("Missing meas_extensions_shapeHSM: lsst_distrib required")

from .MaskedCCD import MaskedCCD
from .AmplifierGeometry import parse_geom_kwd

def make_ccd_mosaic(infile, bias_frame=None, gains=None, fit_order=1):
    """Combine amplifier image arrays into a single mosaic CCD image array."""
    ccd = MaskedCCD(infile, bias_frame=bias_frame)
    foo = fits.open(infile)
    datasec = parse_geom_kwd(foo[1].header['DATASEC'])
    nx_segments = 8
    ny_segments = 2
    nx = nx_segments*(datasec['xmax'] - datasec['xmin'] + 1)
    ny = ny_segments*(datasec['ymax'] - datasec['ymin'] + 1)
    mosaic = np.zeros((ny, nx), dtype=np.float32) # swap x/y to get to camera coordinates

    for ypos in range(ny_segments):
        for xpos in range(nx_segments):
            amp = ypos*nx_segments + xpos + 1

            detsec = parse_geom_kwd(foo[amp].header['DETSEC'])
            xmin = nx - max(detsec['xmin'], detsec['xmax'])
            xmax = nx - min(detsec['xmin'], detsec['xmax']) + 1
            ymin = ny - max(detsec['ymin'], detsec['ymax'])
            ymax = ny - min(detsec['ymin'], detsec['ymax']) + 1
            #
            # Extract bias-subtracted image for this segment
            #
            segment_image = ccd.unbiased_and_trimmed_image(amp, fit_order=fit_order)
            subarr = segment_image.getImage().getArray()
            #
            # Determine flips in x- and y- direction
            #
            if detsec['xmax'] > detsec['xmin']: # flip in x-direction
                subarr = subarr[:, ::-1]
            if detsec['ymax'] > detsec['ymin']: # flip in y-direction
                subarr = subarr[::-1, :]
            #
            # Convert from ADU to e-
            #
            if gains is not None:
                subarr *= gains[amp]
            #
            # Set sub-array to the mosaiced image
            #
            mosaic[ymin:ymax, xmin:xmax] = subarr

    image = afwImage.ImageF(mosaic)
    return image

class SpotConfig(pexConfig.Config):
    """Configuration for Spot analysis task"""
    minpixels = pexConfig.Field("Minimum number of pixels above detection threshold", 
                                int, default=10)
    nsig = pexConfig.Field("Source footprint threshold in number of standard deviations of image section", 
                           float, default=10)
    temp_set_point = pexConfig.Field("Required temperature (C) set point",
                                     float, default=-95.)
    temp_set_point_tol = pexConfig.Field("Required temperature set point tolerance (degrees C)",
                                         float, default=1.)
    output_dir = pexConfig.Field("Output directory", str, default='.')
    output_file = pexConfig.Field("Output filename", str, default=None)
    verbose = pexConfig.Field("Turn verbosity on", bool, default=True)

class SpotTask(pipeBase.Task):
    """Task to estimate spot moments from spot projector data."""

    ConfigClass = SpotConfig
    _DefaultName = "SpotTask"

    @pipeBase.timeMethod
    def run(self, sensor_id, infile, gains, bias_frame=None,
            oscan_fit_order=1):
        imutils.check_temperatures(infiles, self.config.temp_set_point_tol,
                                   setpoint=self.config.temp_set_point,
                                   warn_only=True)

        if self.config.verbose and spot_catalog is None:
            self.log.info("Input files:")
            self.log.info("  {0}".format(infile))
        #
        # Set up characterize task configuration
        #
        nsig = self.config.nsig
        minpixels = self.config.minpixels
        charConfig = CharacterizeImageConfig()
        charConfig.doMeasurePsf = False
        charConfig.doApCorr = False
        charConfig.repair.doCosmicRay = False
        charConfig.detection.minPixels = minpixels
        charConfig.detection.background.binSize = 10
        charConfig.detection.thresholdType = "stdev"
        charConfig.detection.thresholdValue = nsig
        try:
            charConfig.measurement.plugins.names |= ["ext_shapeHSM_HsmSourceMoments"]
        except KeyError:
            pass
        charTask = CharacterizeImageTask(config=charConfig)
        #
        # Process a mosaiced CCD image
        #
        if self.config.verbose:
            self.log.info("processing {0}".format(infile))
        image = make_ccd_mosaic(infile, bias_frame=bias_frame, gains=gains,
                                fit_order=oscan_fit_order)
        exposure = afwImage.ExposureF(image.getBBox())
        exposure.setImage(image)
        result = charTask.characterize(exposure)
        src = result.sourceCat
        if self.config.verbose:
            self.log.info("Detected {0} objects".format(len(src)))
        #
        # Save catalog results to file
        #
        output_dir = self.config.output_dir
        if self.config.output_file is None:
            output_file = os.path.join(output_dir,
                                       '{0}_source_catalog_nsig{1}.cat'.format(sensor_id, nsig))
        else:
            output_file = os.path.join(output_dir, self.config.output_file)
        if self.config.verbose:
            self.log.info("Writing spot results file to {0}".format(output_file))
        src.writeFits(output_file)
