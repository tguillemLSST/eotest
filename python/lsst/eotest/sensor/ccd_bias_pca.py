"""
Module to use PCA modeling of bias frames.   This code is based on a
jupyter notebook from Andrew Bradshaw.
"""
import os
import pickle
import numpy as np
from astropy.io import fits
from sklearn.decomposition import PCA
import lsst.afw.image as afwImage
import lsst.afw.math as afwMath
import lsst.afw.detection as afwDetect
import lsst.ip.isr as ipIsr
import lsst.eotest.image_utils as imutils
from lsst.eotest.fitsTools import fitsWriteto
from .AmplifierGeometry import makeAmplifierGeometry


__all__ = ['CCD_bias_PCA', 'defect_repair', 'pca_superbias']


def pca_superbias(bias_files, pca_bias_files, outfile, overwrite=True,
                  statistic=afwMath.MEDIAN):
    """
    Compute a "superbias" frame from a set of bias files with the
    PCA-biased correction applied to each image.

    Parameters
    ----------
    bias_files: list-like
        List of single CCD bias files.
    pca_bias_files: (str, str)
        Two member tuple of strings. The first item is the pickle file
        containing the PCA model components, and the second item is
        the file containing the mean bias images per amp used in the
        modeling.
    outfile: str
        Name of the output FITS file.
    overwrite: bool [True]
        Option to overwrite the outfile.
    statistic: lsst.afw.math.statistics.Property [lsst.afw.math.MEDIAN]
        Statistic to use with lsst.afw.math.statisticsStack for producing
        the superbias frame.
    """
    ccd_pcas = CCD_bias_PCA.read_model(*pca_bias_files)
    if not hasattr(ccd_pcas, 'mean_amp_cache'):
        ccd_pcas.mean_amp_cache = None
    amps = imutils.allAmps(bias_files[0])
    medianed_images = dict()
    for amp in amps:
        images = []
        for bias_file in bias_files:
            imarr = np.array(fits.getdata(bias_file, amp), dtype=np.float32)
            imarr -= ccd_pcas.pca_bias_correction(amp, imarr)
            images.append(afwImage.ImageF(imarr))
        medianed_images[amp] = afwMath.statisticsStack(images, statistic)
    with fits.open(bias_files[0]) as hdus:
        for amp, image in medianed_images.items():
            hdus[amp].data = image.array
        hdus[0].header['FILENAME'] = os.path.basename(outfile)
        fitsWriteto(hdus, outfile, overwrite=overwrite)


def defect_repair(imarr, sigma=10, nx=10, ny=10, grow=2, use_abs_image=False):
    """Repair pixel defects in an array of pixel data.

    Parameters
    ----------
    imarr: np.array
        2D array of pixel data.
    sigma: float [10]
        Number of clipped stdevs to use for the defect detection threshold.
    nx: int [10]
        Size in pixels of local background region in x-direction.
    ny: int [10]
        Size in pixels of local background region in y-direction.
    grow: int [2]
        Number of pixels to grow the initial footprint for each detection.
    use_abs_image: bool [False]
        Take the absolute value of the background-subtracted image to
        find positive and negative defects.

    Returns
    -------
    numpy.ma.MaskedArray

    Algorithm
    ---------
    * A local background model, based on pixel neighborhoods of size nx x ny,
      is subtracted from the raw data.
    * A clipped stdev is computed from the background-subtracted data, and
      a threshold of sigma*clipped_stdev is computed.
    * Defect footprints are found by applying that threshold to the `np.abs`
      of the background-subtracted data.
    * The footprints are grown by a `grow` pixels to handle below-threshold
      signal "leakage" around the boundary of original footprint.
    * A "BAD" pixel mask is created from the grown footprints.
    * The original image data is interpolated across the masked regions.
    """
    # Create an lsst.afw.image.ImageF object so that the afw tools
    # can be used.
    image = afwImage.ImageF(np.array(imarr, dtype=np.float32))

    # Do local background modeling and subtraction.
    nbins_x = max(10, imarr.shape[1]//nx)
    nbins_y = max(10, imarr.shape[0]//ny)
    bg_ctrl = afwMath.BackgroundControl(nbins_x, nbins_y)
    image -= afwMath.makeBackground(image, bg_ctrl).getImageF()

    # Compute the detection threshold using the clipped stdev.
    stats = afwMath.makeStatistics(image, afwMath.STDEVCLIP)
    stdev = stats.getValue(afwMath.STDEVCLIP)
    threshold = afwDetect.Threshold(sigma*stdev)

    # Take the absolute value of image array to detect outlier pixels
    # with both positive and negative values.
    abs_image = image.Factory(image, deep=True)
    if use_abs_image:
        abs_image.array = np.abs(image.array)

    # Generate footprints for the above-threshold pixels and grow them
    # by `grow` pixels.
    fpset = afwDetect.FootprintSet(abs_image, threshold)
    fpset = afwDetect.FootprintSet(fpset, grow, False)

    # Make a mask and set the bad pixels.
    mask = afwImage.Mask(image.getDimensions())
    mask_name = 'BAD'
    fpset.setMask(mask, mask_name)

    # Create a MaskedImage from the original data.
    mi = afwImage.MaskedImageF(
        afwImage.ImageF(np.array(imarr, dtype=np.float32)), mask)
    fwhm = 1
    out_image = ipIsr.interpolateFromMask(mi, fwhm, maskNameList=[mask_name])\
                     .getImage()

    # Convert to a numpy.ma.MaskedArray and return
    return np.ma.MaskedArray(data=out_image.array, mask=(mask.array == 1))


def get_amp_stack(fits_files, amp, sigma=10, nx=10, ny=10, grow=2):
    """Get a list of numpy arrays of pixel data for the specified amp.

    Parameters
    ----------
    fits_files: list
        List of FITS filenames.
    amp: int
        Desired amp.
    sigma: float [10]
        Numer of standard deviations to use in sigma-clipping mask
        applied to each frame.  If None, then no masking will be
        performed.
    nx: int [10]
        Size in pixels of local background region in x-direction.
    ny: int [10]
        Size in pixels of local background region in y-direction.
    grow: int [2]
        Number of pixels to grow the above-threshold footprints
        for mask generation.

    Returns
    -------
    numpy array of a stack of amp imaging section pixel data
    """
    amp_stack = []
    for item in fits_files:
        with fits.open(item) as hdus:
            if sigma is None:
                imarr = hdus[amp].data
            else:
                imarr = defect_repair(hdus[amp].data, sigma=sigma,
                                      nx=nx, ny=ny, grow=grow)
            amp_stack.append(np.array(imarr, dtype=float))
    return np.array(amp_stack)


class CCD_bias_PCA(dict):
    """
    Class to compute mean bias frames and PCA-based models of the overscan
    subtraction derived from an ensemble of bias frames.
    """
    def __init__(self, nx=10, ny=10, std_max=10, xstart=None, ystart=0,
                 ncomp_x=6, ncomp_y=8):
        """
        Parameters
        ----------
        nx: int [10]
            Size in pixels of local background region in x-direction.
        ny: int [10]
            Size in pixels of local background region in y-direction.
        std_max: float [10]
            Cutoff for stdev of amp ADU values for inclusion in the PCA
            training set.
        xstart: int [None]
            Starting pixel for the PCA modeling in the serial direction.
            If None, then use the number of prescan pixels, 3 for ITL
            and 10 for e2V.
        ystart: int [0]
            Starting pixel for the PCA modeling in the parallel direction.
        ncomp_x: int [6]
            Number of PCA components to fit in the serial direction.
        ncomp_y: int [8]
            Number of PCA components to fit in the parallel direction.
        """
        super().__init__()
        self.nx = nx
        self.ny = ny
        self.std_max = std_max
        self.xstart = xstart
        self.ystart = ystart
        self.ncomp_x = ncomp_x
        self.ncomp_y = ncomp_y
        self.x_oscan_corner = None
        self.y_oscan_corner = None
        self.pca_bias_file = None
        self.mean_amp_cache = None

    def compute_pcas(self, fits_files, outfile_prefix, amps=None,
                     verbose=False, fit_full_segment=True, sigma=10,
                     grow=2, use_median=True):
        """
        Compute mean bias and PCA models of serial and parallel
        overscans using a list of bias frame FITS files for a
        particular CCD.

        Parameters
        ----------
        fits_files: list
            List of bias frame FITS files for a single CCD.
        outfile_prefix: str
            Prefix of output files containing the mean/median bias frame,
            `f'{outfile_prefix}_pca_bias.fits'`, and the pickle file
            containing the PCA models, `f'{outfile_prefix}_pca_bias.pickle'`.
        amps: list-like [None]
            A list of amps to model. If None, then do all amps in the CCD.
        verbose: bool [False]
            Flag to print the progress of computing PCAs for each amp.
        fit_full_segment: bool [True]
            Use the full amplifier segment in deriving the PCAs.  If False,
            then use the parallel and serial overscan regions.
        sigma: float [10]
            Value to use for sigma-clipping the amp-level images that
            are included in the training set.
        grow: int [2]
            Number of pixels to grow the above-threshold footprints
            for mask generation.
        use_median: bool [True]
            Compute the median of the stacked images for the mean_amp
            image.  If False, then compute the mean.
        """
        amp_geom = makeAmplifierGeometry(fits_files[0])
        self.x_oscan_corner = amp_geom.imaging.getEndX()
        self.y_oscan_corner = amp_geom.imaging.getEndY()
        if self.xstart is None:
            self.xstart = amp_geom.imaging.getBeginX()
        if amps is None:
            amps = imutils.allAmps(fits_files[0])
        with fits.open(fits_files[0]) as mean_bias_frame:
            for amp in amps:
                if verbose:
                    print(f"amp {amp}")
                amp_stack = get_amp_stack(fits_files, amp, sigma=sigma,
                                          nx=self.nx, ny=self.ny, grow=grow)
                pcax, pcay, mean_amp \
                    = self._compute_amp_pcas(amp_stack,
                                             fit_full_segment=fit_full_segment,
                                             verbose=verbose,
                                             use_median=use_median)
                self[amp] = pcax, pcay
                mean_bias_frame[amp].data = mean_amp
            self.pca_bias_file = f'{outfile_prefix}_pca_bias.fits'
            mean_bias_frame[0].header['FILENAME'] = self.pca_bias_file
            fitsWriteto(mean_bias_frame, self.pca_bias_file, overwrite=True)
        pickle_file = f'{outfile_prefix}_pca_bias.pickle'
        self.to_pickle(pickle_file)
        return pickle_file, self.pca_bias_file

    def _compute_amp_pcas(self, amp_stack, fit_full_segment=True,
                          verbose=False, use_median=True):
        # Compute the me[di]an bias image from the stack of amp data.
        if use_median:
            mean_amp = np.median(amp_stack, axis=0)
        else:
            mean_amp = np.mean(amp_stack, axis=0)
        if verbose:
            print("np.std(me[di]an_amp):", np.std(mean_amp))

        # Assemble the training set of mean-subtracted images from the
        # stack of raw amplifier data.  Also subtract the me[di]an of the
        # per-amp overscan corner from each image, and apply a noise
        # cut of self.std_max for inclusion in the training set.
        imarrs = []
        stdevs = []
        for i, _ in enumerate(amp_stack):
            imarr = _.copy()
            imarr -= mean_amp
            imarr -= self.mean_oscan_corner(imarr)
            stdevs.append(np.std(imarr))
            imarrs.append(imarr)
        std_max = max(self.std_max, np.percentile(stdevs, 80))
        training_set = []
        for i, (stdev, imarr) in enumerate(zip(stdevs, imarrs)):
            if stdev <= std_max:
                training_set.append(imarr)
            else:
                print('_compute_amp_pcas: rejected frame:',
                      i, stdev, std_max)

        if verbose:
            print("training set size:", len(training_set))

        # Create the ensemble of profiles in the serial direction.
        if fit_full_segment:
            # This uses the full data segment, rather than just the parallel
            # overscan.
            x_profs = [np.mean(_, axis=0)[self.xstart:] for _ in training_set]
        else:
            # Use the parallel overscan region instead of full
            # segment.
            x_profs = [np.mean(_[self.y_oscan_corner:, :], axis=0)[self.xstart:]
                       for _ in training_set]

        # Run the PCA fit for the serial direction
        pcax = PCA(self.ncomp_x)
        pcax.fit(x_profs)

        # Use the previous serial direction decomposition to do the
        # fitting in the parallel direction.
        y_profs = []
        for imarr in training_set:
            # Build the serial model, using the pcax basis set, and fit
            # to the full segment data in the serial direction.
            _ = pcax.transform(imarr[self.ystart:, self.xstart:])
            proj = pcax.inverse_transform(_)
            serial_model = np.mean(proj, axis=0)

            # Subtract the serial model from the me[di]an-subtracted training
            # image.
            new_imarr = imarr.copy()[self.ystart:, self.xstart:] - serial_model

            # Add the resulting profile to the y-ensemble
            if fit_full_segment:
                y_profs.append(np.mean(new_imarr, axis=1))
            else:
                # Just use the serial overscan data.
                y_profs.append(np.mean(new_imarr[:, self.x_oscan_corner:],
                                       axis=1))


        # Run the PCA fit for the parallel direction.
        pcay = PCA(self.ncomp_y)
        pcay.fit(y_profs)

        return pcax, pcay, mean_amp

    def mean_oscan_corner(self, imarr, buff=0):
        """
        Compute the mean pixel value of the region common to
        the parallel and serial overscan regions.

        Parameters
        ----------
        imarr: numpy.array
            Array of pixel values from the full segment of the amplifier.
        buff: int [2]
            Buffer pixels to offset from the imaging region for computing the
            mean value, e.g., to avoid trailed charge.

        Returns
        -------
        float
        """
        return np.mean(imarr[self.y_oscan_corner + buff:,
                             self.x_oscan_corner + buff:])

    def to_pickle(self, outfile):
        """
        Write the CCD_bias_PCA object as a pickle object.

        Parameters
        ----------
        outfile: str
            Filename of output pickle file.
        """
        with open(outfile, 'wb') as fd:
            pickle.dump(self, fd)

    @staticmethod
    def read_pickle(infile):
        """
        Read a CCD_bias_PCA object from a pickle file.

        Parameters
        ----------
        infile: str
            Filename of input pickle file.

        Returns
        -------
        CCD_bias_PCA object.
        """
        with open(infile, 'rb') as fd:
            my_instance = pickle.load(fd)
        return my_instance

    @staticmethod
    def read_model(pca_model_file, pca_bias_file):
        """
        Read in the PCA model and associated PCA bias frame for computing
        the bias corrections.

        Parameters
        ----------
        pca_model_file: str
            Pickle file containing the PCA model of the bias correction.
        pca_bias_file: str
            FITS file containing the mean images of each amplifier that
            were used to fit the PCA model.

        Returns
        -------
        CCD_bias_PCA object with the pca_bias_file FITS file explicitly
        set.
        """
        my_instance = CCD_bias_PCA.read_pickle(pca_model_file)
        my_instance.pca_bias_file = pca_bias_file
        return my_instance

    def pca_bias_correction(self, amp, image_array):
        """
        Compute the bias model based on the PCA fit.  This should be
        subtracted from the raw data for the specified amp in order to
        apply an overscan+bias correction.

        Parameters
        ----------
        amp: int
            Amplifier for which to compute the correction.
        image_array: numpy.array
            Array containing the pixel values for the full segment of
            the specified amp.

        Returns
        -------
        numpy.array: Array with the pixel values of the computed correction
            for the full segment.

        """
        pcax, pcay = self[amp]
        if self.mean_amp_cache is None or self.mean_amp_cache[0] != amp:
            mean_amp = fits.getdata(self.pca_bias_file, amp).astype('float')
            self.mean_amp_cache = (amp, mean_amp)
        else:
            mean_amp = self.mean_amp_cache[1]

        imarr = image_array - mean_amp

        # Run defect repair on overscan regions.
        ny, nx = imarr.shape

        # Parallel overscan region:
        yslice = slice(self.y_oscan_corner, ny)
        imarr[yslice, :] = defect_repair(imarr[yslice, :]).data

        # Serial overscan region:
        xslice = slice(self.x_oscan_corner, nx)
        imarr[:, xslice] = defect_repair(imarr[:, xslice]).data

        corner_mean = self.mean_oscan_corner(imarr)
        imarr -= corner_mean

        # Build the serial PCA-based model using the parallel overscan
        _ = pcax.transform(imarr[self.y_oscan_corner:, self.xstart:])
        projx = pcax.inverse_transform(_)
        serial_model = np.mean(projx, axis=0)

        # Build the parallel PCA-based model using the serial overscan
        # after subtracting the serial_model
        imarr[self.ystart:, self.xstart:] -= serial_model
        _ = pcay.transform(imarr[self.ystart:, self.x_oscan_corner:].T)
        projy = pcay.inverse_transform(_)
        parallel_model = np.mean(projy, axis=0)

        bias_model = mean_amp + corner_mean
        bias_model[self.ystart:, self.xstart:] += serial_model
        bias_model = (bias_model.T + parallel_model).T
        bias_model += self.mean_oscan_corner(image_array - bias_model)

        return bias_model

    @staticmethod
    def bbox_chisq(amp_image, bias_model, bbox):
        """
        Compute the chi-square for the pixels in the provided bounding box,
        using the bias_model value as the variance.
        """
        data = amp_image.Factory(amp_image, bbox, deep=True)
        model = bias_model.Factory(bias_model, bbox)
        data -= model
        chisq = np.sum(data.array*data.array/model.array)
        return chisq

    def make_bias_frame(self, raw_file, outfile, residuals_file=None,
                        amps=None):
        """
        Construct the PCA model bias frame for one of the bias files
        and optionally write the bias-subtracted file.

        Parameters
        ----------
        raw_file: str
            Filename of raw single CCD FITS file.
        outfile: str
            Filename of the output bias frame.
        residuals_file: str [None]
            Filename of the output bias-subtracted frame. If None, then
            the file is not written.
        """
        if amps is None:
            amps = imutils.allAmps(raw_file)
        with fits.open(raw_file) as hdus:
            for amp in amps:
                hdus[amp].data = self.pca_bias_correction(amp, hdus[amp].data)
            fitsWriteto(hdus, outfile, overwrite=True)

        if residuals_file is not None:
            with fits.open(raw_file) as resids, fits.open(outfile) as bias:
                for amp in amps:
                    resids[amp].data = (np.array(resids[amp].data, dtype=float)
                                        - bias[amp].data)
                resids.writeto(residuals_file, overwrite=True)
