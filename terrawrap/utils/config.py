"""Holds config utilities"""
import os
import re
import subprocess

import hcl
import jsons as jsons
import yaml
from ssm_cache import SSMParameterGroup

from terrawrap.models.wrapper_config import WrapperConfig
from terrawrap.utils.collection_utils import update

GIT_REPO_REGEX = r"URL.*/([\w-]*)(?:\.git)?"
DEFAULT_REGION = 'us-west-2'
SSM_ENVVAR_CACHE = SSMParameterGroup(max_age=600)


def find_variable_files(path):
    """
    Convenience function for finding all Terraform variable files by walking a given path.
    :param path: The path to the Terraform configuration directory.
    :return: A list of Terraform variable files that can be found by walking down that path, in order of
    discovery from the root of the path.
    """
    variable_files = []

    elements = path.split(os.path.sep)

    cur_path = os.path.sep

    for element in elements:
        cur_path = os.path.join(cur_path, element)
        for file in os.listdir(cur_path):
            if file.endswith(".auto.tfvars"):
                variable_files.append(os.path.join(cur_path, file))

    return variable_files


def find_wrapper_config_files(path):
    """
    Convenience function for finding all wrapper config files by walking a given path.
    :param path: The path to the Terraform configuration directory.
    :return: A list of wrapper config files that can be found by walking down that path, in order of
    discovery from the root of the path.
    """
    wrapper_config_files = []

    elements = path.split(os.path.sep)

    cur_path = os.path.sep

    for element in elements:
        cur_path = os.path.join(cur_path, element)
        for file in os.listdir(cur_path):
            if file.endswith(".tf_wrapper") or file.endswith(".tf_wrapper.yml"):
                wrapper_config_files.append(os.path.join(cur_path, file))

    return wrapper_config_files


def parse_wrapper_configs(wrapper_config_files) -> WrapperConfig:
    """
    Function for parsing the Terraform wrapper config file.
    :param wrapper_config_files: A list of file paths to wrapper config files. Config files later in the list
    override those earlier in the list, and are merged with the default config and earlier files.
    :return: A dictionary of the Terraform wrapper config file, or the default config object.
    """
    generated_wrapper_config = {
        "configure_backend": True,
        "pipeline_check": True,
        "resolved_envvars": {},
        "envvars": {}
    }

    for wrapper_config_path in wrapper_config_files:
        with open(wrapper_config_path) as wrapper_config_file:
            wrapper_config = yaml.safe_load(wrapper_config_file)
            generated_wrapper_config = update(generated_wrapper_config, wrapper_config)

    wrapper_config_obj: WrapperConfig = jsons.load(generated_wrapper_config, WrapperConfig, strict=True)
    return wrapper_config_obj


def resolve_envvars(envvar_configs):
    """
    Resolves the 'envvars' section from the wrapper config to actual environment variables that can be easily
    supplied to a command.
    :param envvar_configs: The 'envvars' dictionary from the wrapper config.
    :return: A dictionary representing the environment variables that were resolved, with the key being the
    name of the environment variable and the value being the value of the environment variable.
    """
    resolved_envvars = {}
    for envvar_name, envvar_config in envvar_configs.items():
        if envvar_config["source"] == "ssm":
            resolved_envvars[envvar_name] = SSM_ENVVAR_CACHE.parameter(envvar_config["path"]).value
    return resolved_envvars


def calc_backend_config(path, variables):
    """
    Convenience function for calculating the backend config of the given Terraform directory.
    :param path: The path to the directory containing the Terraform config.
    :param variables: The variables derived from the auto.tfvars files.
    :return: A dictionary representing the backend configuration for the Terraform directory.
    """
    terraform_bucket = "{region}--mclass--terraform--{account_short_name}".format(
        region=variables['region'],
        account_short_name=variables['account_short_name']
    )

    output = subprocess.check_output(["git", "remote", "show", "origin", "-n"], cwd=path).decode("utf-8")
    match = re.search(GIT_REPO_REGEX, output)
    if match:
        repo_name = match.group(1)
    else:
        raise RuntimeError("Could not determine git repo name, are we in a git repo?")

    backend_config = [
        "-reconfigure",
        "-backend-config=dynamodb_table=%s" % variables.get('terraform_lock_table', 'terraform-locking'),
        "-backend-config=encrypt=true",
        "-backend-config=key=%s/%s.tfstate" % (repo_name, path[path.index("/config") + 1:]),
        "-backend-config=region=%s" % variables['region'],
        "-backend-config=bucket=%s" % variables.get('terraform_state_bucket', terraform_bucket),
        "-backend-config=skip_get_ec2_platforms=true",  # Not needed if we have ec2:DescribeAccountAttributes
        "-backend-config=skip_region_validation=true",  # We don't need to validate that our region is correct
        "-backend-config=skip_credentials_validation=true",  # This solves the TLS errors we've seen
    ]

    return backend_config


def parse_variable_files(variable_files):
    """
    Convenience function for parsing variable files and returning the variables as a dictionary
    :param variable_files: List of file paths to variable files. Variable files overwrite files before them.
    :return: A dictionary representing the contents of those variable files.
    """
    variables = {}

    for variable_file in variable_files:
        with open(variable_file) as var_file:
            variables.update(hcl.load(var_file))

    return variables
