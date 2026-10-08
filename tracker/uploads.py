from django.core.files.uploadhandler import FileUploadHandler, SkipFile


class SkipFilesUploadHandler(FileUploadHandler):
    """Skip every uploaded file without storing it, for DOCUMENT_UPLOADS_ENABLED=false.

    Django then reads past the file in small chunks, so it is neither written to
    a temporary file nor held in memory, and the rest of the form still arrives.
    """

    def new_file(self, *args, **kwargs):
        raise SkipFile

    def receive_data_chunk(self, raw_data, start):
        return None

    def file_complete(self, file_size):
        return None
