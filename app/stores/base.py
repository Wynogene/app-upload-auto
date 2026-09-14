from app.models import OperationResult, Platform, ReviewStatus, SubmitRequest, UploadRequest


class StoreClient:
    """Platform store adapter interface."""

    platform: Platform

    def upload(self, req: UploadRequest, app_cfg: dict) -> OperationResult:
        raise NotImplementedError

    def submit(self, req: SubmitRequest, app_cfg: dict) -> OperationResult:
        raise NotImplementedError

    def status(self, app_cfg: dict, version_name: str | None = None) -> ReviewStatus:
        raise NotImplementedError
