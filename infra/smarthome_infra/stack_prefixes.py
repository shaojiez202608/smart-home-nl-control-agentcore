"""Constants shared by the stack and the deployment scripts (no CDK import)."""

# Geographic prefixes of cross-Region inference profile ids (for example "us." in us.amazon.nova-lite-v1:0).
INFERENCE_PROFILE_PREFIXES = ("us", "eu", "apac", "global", "jp", "au", "ca", "us-gov")


def base_model_id(model_id: str) -> str:
    head, _, rest = model_id.partition(".")
    return rest if head in INFERENCE_PROFILE_PREFIXES and rest else model_id
