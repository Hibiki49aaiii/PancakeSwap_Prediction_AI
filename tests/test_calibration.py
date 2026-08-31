from pancake_prediction.calibration import CalibrationPoint, fit_histogram_calibrator


def test_histogram_calibrator_shrinks_sparse_bins_to_global_rate() -> None:
    model = fit_histogram_calibrator(
        (
            CalibrationPoint(100_000, 0),
            CalibrationPoint(150_000, 0),
            CalibrationPoint(850_000, 1),
            CalibrationPoint(900_000, 1),
        ),
        bins=10,
        shrinkage=10,
        train_max_epoch=99,
    )
    assert model.global_probability_ppm == 500_000
    assert model.predict_ppm(500_000) == 500_000
    assert model.predict_ppm(900_000) > 500_000
    assert model.train_max_epoch == 99
