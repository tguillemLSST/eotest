"""
@brief Class to handle masks for a full sensor.  Added mask files can be
or'd to existing masks, and mask bits can be set for use with an
afwMath.makeStatistics object.

@author J. Chiang <jchiang@slac.stanford.edu>
"""
import pyfits
from AmplifierGeometry import makeAmplifierGeometry
import lsst.daf.base as dafBase
import lsst.afw.geom as afwGeom
import lsst.afw.image as afwImage
import lsst.afw.math as afwMath
import lsst.pex.exceptions as pexExcept

import lsst.eotest.image_utils as imutils

class Metadata(object):
    def __init__(self, infile, hdu):
        self.header = None
        try:
            self.md = afwImage.readMetadata(infile, hdu)
        except pexExcept.LsstCppException:
            # This exception occurs when DM stack encounters a "." in
            # a FITS header keyword.
            self.header = pyfits.open(infile)[hdu-1].header
    def get(self, key):
        return self(key)
    def __call__(self, key):
        if self.header is None:
            return self.md.get(key)
        else:
            return self.header[key]

class MaskedCCDBiasImageException(RuntimeError):
    def __init__(self, *args):
        super(MaskedCCDBiasImageException, self).__init__(*args)

class MaskedCCD(dict):
    """
    This is the main abstraction for handling CCD data in the sensor
    acceptance test scripts.  The pixel data for each segment is
    represented by a MaskedImageF object and are accessed via the
    amplifier number.  Masks can be added and manipulated separately
    by various methods.
    """
    def __init__(self, imfile, mask_files=()):
        dict.__init__(self)
        self.imfile = imfile
        #self.md = afwImage.readMetadata(imfile, 1)
        self.md = Metadata(imfile, 1)
        self.amp_geom = makeAmplifierGeometry(imfile)
        for amp in imutils.allAmps:
            decorated_image = afwImage.DecoratedImageF(imfile,
                                                       imutils.dm_hdu(amp))
            image = decorated_image.getImage()
            mask = afwImage.MaskU(image.getDimensions())
            self[amp] = afwImage.MaskedImageF(image, mask)
        for mask_file in mask_files:
            self.add_masks(mask_file)
        self.stat_ctrl = afwMath.StatisticsControl()
        if mask_files:
            self.setAllMasks()
    def mask_plane_dict(self):
        amp = self.keys()[0]
        return dict(self[amp].getMask().getMaskPlaneDict().items())
    def add_masks(self, mask_file):
        """
        Add a masks from a mask file by or-ing with existing masks.
        """
        for amp in imutils.allAmps:
            curr_mask = self[amp].getMask()
            curr_mask |= afwImage.MaskU(mask_file, imutils.dm_hdu(amp))
    def setMask(self, mask_name=None, clear=False):
        """
        Enable a mask and return the afwMath.StatisticsControl object
        for use by afwMath.makeStatistics. If clear is False, then the
        new mask is or'd with the existing mask.  If clear is False
        and mask_name is None, then all mask bits are cleared.
        """
        if clear:                         # Unset all masks.
            self.stat_ctrl.setAndMask(0)
        if mask_name is not None:         # Set the desired mask.
            new_mask = (self.stat_ctrl.getAndMask()
                        | afwImage.MaskU.getPlaneBitMask(mask_name))
            self.stat_ctrl.setAndMask(new_mask)
        return self.stat_ctrl
    def setAllMasks(self):
        "Enable all masks."
        mpd = self.mask_plane_dict()
        mask_bits = 2**len(mpd) - 1
        self.stat_ctrl.setAndMask(mask_bits)
        return self.stat_ctrl
    def bias_image(self, amp, overscan=None, fit_order=1):
        """
        Use separately stored metadata to determine file-specified
        overscan region.
        """
        if overscan is None:
            overscan = self.amp_geom.serial_overscan
        try:
            return imutils.bias_image(self[amp], overscan=overscan,
                                      fit_order=fit_order)
        except pexExcept.LsstCppException:
            raise MaskedCCDBiasImageException("DM stack error generating bias "
                                              + "image from overscan region.")
    def bias_subtracted_image(self, amp, overscan=None, fit_order=1):
        self[amp] -= self.bias_image(amp, overscan, fit_order)
        return self[amp]
    def unbiased_and_trimmed_image(self, amp, overscan=None,
                                   imaging=None, fit_order=1):
        """
        Use separately stored metadata to determine file-specified
        overscan and imaging regions.
        """
        if overscan is None:
            overscan = self.amp_geom.serial_overscan
        if imaging is None:
            imaging = self.amp_geom.imaging
        return imutils.unbias_and_trim(self[amp], overscan=overscan,
                                       imaging=imaging, fit_order=fit_order)

def add_mask_files(mask_files, outfile, clobber=True):
    masks = dict([(amp, afwImage.MaskU(mask_files[0], imutils.dm_hdu(amp)))
                  for amp in imutils.allAmps])
    for mask_file in mask_files[1:]:
        for amp in imutils.allAmps:
            masks[amp] |= afwImage.MaskU(mask_file, imutils.dm_hdu(amp))
    output = pyfits.HDUList()
    output.append(pyfits.PrimaryHDU())
    output.writeto(outfile, clobber=clobber)
    for amp in imutils.allAmps:
        md = dafBase.PropertySet()
        md.set('EXTNAME', 'SEGMENT%s' % imutils.channelIds[amp])
#        md.set('DETSIZE', imutils.detsize)
#        md.set('DETSEC', imutils.detsec(amp))
        masks[amp].writeFits(outfile, md, 'a')
    return masks

def compute_stats(image, sctrl, weights=None):
    flags = afwMath.MEAN | afwMath.STDEV
    if weights is None:
        stats = afwMath.makeStatistics(image, flags, sctrl)
    else:
        stats = afwMath.makeStatistics(image, weights, flags, sctrl)
    return stats.getValue(afwMath.MEAN), stats.getValue(afwMath.STDEV)

if __name__ == '__main__':
    image_file = 'bright_pix_test.fits'
    mask_files = ('bright_pix_mask.fits', 'CCD250_DEFECTS_mask.fits')

    ccd = MaskedCCD(image_file)
    for mask_file in mask_files:
        print "adding masks from", mask_file
        ccd.add_masks(mask_file)
        print "mask plane dict:", ccd.mask_plane_dict()
        print

    amp = imutils.allAmps[0]
    
    sctrl = ccd.stat_ctrl
    print sctrl.getAndMask(), compute_stats(ccd[amp], sctrl)

    sctrl = ccd.setMask('BAD')
    print sctrl.getAndMask(), compute_stats(ccd[amp], sctrl)

    sctrl = ccd.setMask('CCD250_DEFECTS')
    print sctrl.getAndMask(), compute_stats(ccd[amp], sctrl)

    sctrl = ccd.setMask(clear=True)
    print sctrl.getAndMask(), compute_stats(ccd[amp], sctrl)

    sctrl = ccd.setAllMasks()
    print sctrl.getAndMask(), compute_stats(ccd[amp], sctrl)