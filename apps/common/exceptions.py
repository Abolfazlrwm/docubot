"""Processing error taxonomy shared by extraction and embedding code."""


class ProcessingError(Exception):
    """Base class for document processing failures."""


class PermanentProcessingError(ProcessingError):
    """The document can never be processed successfully; do not retry."""


class TransientProcessingError(ProcessingError):
    """A temporary failure (network, rate limit); a retry may succeed."""
