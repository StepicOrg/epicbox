import docker

from . import config


def get_docker_client():
    return docker.Client(base_url=config.DOCKER_URL)


def filter_filenames(files):
    # TODO: extract only file names from the files list
    return files


def merge_limits_defaults(limits):
    if not limits:
        return config.DEFAULT_LIMITS
    is_realtime_specified = 'realtime' in limits
    for limit_name, default_value in config.DEFAULT_LIMITS.items():
        if limit_name not in limits:
            limits[limit_name] = default_value
    if not is_realtime_specified:
        limits['realtime'] = limits['cputime'] * config.CPU_TO_REAL_TIME_FACTOR
    return limits


def truncate_result(result):
    MAX_OUTPUT_LENGTH = 100
    truncated = {}
    for k, v in result.items():
        if k in ['stdout', 'stderr']:
            if len(v) > MAX_OUTPUT_LENGTH:
                v = v[:MAX_OUTPUT_LENGTH] + b' *** truncated ***'
        truncated[k] = v
    return truncated
