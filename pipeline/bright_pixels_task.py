"""
@brief Bright pixels task: Find pixels and columns in a median image
constructed from an ensemble of darks.  The bright pixel threshold is
specified via the --ethresh option and is in units of -e per pixel per
second.  The threshold for the number of bright pixels that define a
bright column is specified via the --colthresh option.

@author J. Chiang <jchiang@slac.stanford.edu>
"""
import os
import numpy as np
import pyfits
import lsst.afw.image as afwImage
import image_utils as imutils
from TaskParser import TaskParser
from BrightPixels import BrightPixels

def _writeFits(images, outfile, md):
    output = pyfits.HDUList()
    output.append(pyfits.PrimaryHDU())
    output[0].header['EXPTIME'] = md.get('EXPTIME')
#    output[0].header['CCD_MANU'] = md.get('CCD_MANU')
#    output[0].header['CCD_TYPE'] = md.get('CCD_TYPE')
#    output[0].header['CCD_SERN'] = md.get('CCD_SERN')
#    output[0].header['LSST_NUM'] = md.get('LSST_NUM')
    for amp in imutils.allAmps:
        output.append(pyfits.ImageHDU(data=images[amp].getArray()))
        output[amp].name = 'AMP%s' % imutils.channelIds[amp]
        output[amp].header.update('DETSIZE', imutils.detsize)
        output[amp].header.update('DETSEC', imutils.detsec(amp))
    output.writeto(outfile, clobber=True)

parser = TaskParser('Find bright pixels and columns')
parser.add_argument('-f', '--dark_files', type=str,
                    help='file pattern for darks')
parser.add_argument('-F', '--dark_file_list', type=str,
                    help='file containing list of dark files')
parser.add_argument('-e', '--ethresh', default=5, type=int,
                    help='bright pixel threshold in e- per pixel per second')
parser.add_argument('-c', '--colthresh', default=20, type=int,
                    help='bright column threshold in # of bright pixels')
parser.add_argument('-p', '--mask_plane', default='BAD', type=str,
                    help='mask plane to be used for output mask file')
parser.add_argument('-t', '--temp_tol', default=1.5, type=float,
                    help='temperature tolerance for CCDTEMP among dark files')
args = parser.parse_args()

dark_files = args.files(args.dark_files, args.dark_file_list)
    
if args.verbose:
    print "processing files: ", dark_files

sensor_id = args.sensor_id
sensor = args.sensor()
gains = args.system_gains()
mask_files = args.mask_files()

imutils.check_temperatures(dark_files, args.temp_tol)

median_images = {}
md = afwImage.readMetadata(dark_files[0], 1)
for amp in imutils.allAmps:
    median_images[amp] = imutils.fits_median(dark_files, imutils.dm_hdu(amp))
medfile = os.path.join(args.output_dir, '%s_median_dark_bp.fits' % sensor_id)
_writeFits(median_images, medfile, md)

bright_pixels = BrightPixels(medfile, mask_files=mask_files,
                             ethresh=args.ethresh, colthresh=args.colthresh,
                             mask_plane=args.mask_plane)

outfile = os.path.join(args.output_dir, '%s_bright_pixel_map.fits' % sensor_id)
total_bright_pixels = 0
print "Segment     # bright pixels"
for amp in imutils.allAmps:
    pixels, columns = bright_pixels.generate_mask(amp, gains[amp], outfile)
    count = len(pixels)
    total_bright_pixels += count
    sensor.add_seg_result(amp, 'numBrightPixels', count)
    print "%s          %i" % (imutils.channelIds[amp], count)

print "Total bright pixels:", total_bright_pixels
sensor.add_ccd_result('numBrightPixels', total_bright_pixels)
