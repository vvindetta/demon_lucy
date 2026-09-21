class VoiceError(Exception):
    def __init__(self, message: str, *, reason: str):
        # Provider and recorder diagnostics can be multiline or very large.
        rendered = " ".join(message.split())
        if len(rendered) > 500:
            rendered = rendered[:500] + "...(clipped)"
        super().__init__(rendered)
        self.reason = reason
