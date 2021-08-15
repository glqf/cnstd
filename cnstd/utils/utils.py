# coding: utf-8
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
import os
import hashlib
import requests
from pathlib import Path
import logging
import platform
import zipfile

from tqdm import tqdm
import cv2
import numpy as np
from PIL import Image
import torch

from ..consts import MODEL_VERSION, BACKBONE_NET_NAME, AVAILABLE_MODELS


fmt = '[%(levelname)s %(asctime)s %(funcName)s:%(lineno)d] %(' 'message)s '
logging.basicConfig(format=fmt)
logging.captureWarnings(True)
logger = logging.getLogger()


def set_logger(log_file=None, log_level=logging.INFO, log_file_level=logging.NOTSET):
    """
    Example:
        >>> set_logger(log_file)
        >>> logger.info("abc'")
    """
    log_format = logging.Formatter(fmt)
    logger.setLevel(log_level)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    logger.handlers = [console_handler]
    if log_file and log_file != '':
        if not Path(log_file).parent.exists():
            os.makedirs(Path(log_file).parent)
        if isinstance(log_file, Path):
            log_file = str(log_file)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_file_level)
        file_handler.setFormatter(log_format)
        logger.addHandler(file_handler)
    return logger


def model_fn_prefix(backbone, epoch):
    return 'cnstd-v%s-%s-%04d.params' % (MODEL_VERSION, backbone, epoch)


def check_context(context):
    if isinstance(context, str):
        return context.lower() in ('gpu', 'cpu', 'cuda')
    if isinstance(context, list):
        if len(context) < 1:
            return False
        return all(isinstance(ctx, torch.device) for ctx in context)
    return isinstance(context, torch.device)


def data_dir_default():
    """

    :return: default data directory depending on the platform and environment variables
    """
    system = platform.system()
    if system == 'Windows':
        return os.path.join(os.environ.get('APPDATA'), 'cnstd')
    else:
        return os.path.join(os.path.expanduser("~"), '.cnstd')


def data_dir():
    """

    :return: data directory in the filesystem for storage, for example when downloading models
    """
    return os.getenv('CNOCR_HOME', data_dir_default())


def check_model_name(model_name):
    assert model_name in BACKBONE_NET_NAME


def check_sha1(filename, sha1_hash):
    """Check whether the sha1 hash of the file content matches the expected hash.
    Parameters
    ----------
    filename : str
        Path to the file.
    sha1_hash : str
        Expected sha1 hash in hexadecimal digits.
    Returns
    -------
    bool
        Whether the file content matches the expected hash.
    """
    sha1 = hashlib.sha1()
    with open(filename, 'rb') as f:
        while True:
            data = f.read(1048576)
            if not data:
                break
            sha1.update(data)

    sha1_file = sha1.hexdigest()
    l = min(len(sha1_file), len(sha1_hash))
    return sha1.hexdigest()[0:l] == sha1_hash[0:l]


def download(url, path=None, overwrite=False, sha1_hash=None):
    """Download an given URL
    Parameters
    ----------
    url : str
        URL to download
    path : str, optional
        Destination path to store downloaded file. By default stores to the
        current directory with same name as in url.
    overwrite : bool, optional
        Whether to overwrite destination file if already exists.
    sha1_hash : str, optional
        Expected sha1 hash in hexadecimal digits. Will ignore existing file when hash is specified
        but doesn't match.
    Returns
    -------
    str
        The file path of the downloaded file.
    """
    if path is None:
        fname = url.split('/')[-1]
    else:
        path = os.path.expanduser(path)
        if os.path.isdir(path):
            fname = os.path.join(path, url.split('/')[-1])
        else:
            fname = path

    if (
            overwrite
            or not os.path.exists(fname)
            or (sha1_hash and not check_sha1(fname, sha1_hash))
    ):
        dirname = os.path.dirname(os.path.abspath(os.path.expanduser(fname)))
        if not os.path.exists(dirname):
            os.makedirs(dirname)

        print('Downloading %s from %s...' % (fname, url))
        r = requests.get(url, stream=True)
        if r.status_code != 200:
            raise RuntimeError("Failed downloading url %s" % url)
        total_length = r.headers.get('content-length')
        with open(fname, 'wb') as f:
            if total_length is None:  # no content length header
                for chunk in r.iter_content(chunk_size=1024):
                    if chunk:  # filter out keep-alive new chunks
                        f.write(chunk)
            else:
                total_length = int(total_length)
                for chunk in tqdm(
                        r.iter_content(chunk_size=1024),
                        total=int(total_length / 1024.0 + 0.5),
                        unit='KB',
                        unit_scale=False,
                        dynamic_ncols=True,
                ):
                    f.write(chunk)

        if sha1_hash and not check_sha1(fname, sha1_hash):
            raise UserWarning(
                'File {} is downloaded but the content hash does not match. '
                'The repo may be outdated or download may be incomplete. '
                'If the "repo_url" is overridden, consider switching to '
                'the default repo.'.format(fname)
            )

    return fname


def get_model_file(model_dir):
    r"""Return location for the downloaded models on local file system.

    This function will download from online model zoo when model cannot be found or has mismatch.
    The root directory will be created if it doesn't exist.

    Parameters
    ----------
    model_dir : str, default $CNOCR_HOME
        Location for keeping the model parameters.

    Returns
    -------
    file_path
        Path to the requested pretrained model file.
    """
    model_dir = os.path.expanduser(model_dir)
    par_dir = os.path.dirname(model_dir)
    os.makedirs(par_dir, exist_ok=True)

    zip_file_path = model_dir + '.zip'
    if not os.path.exists(zip_file_path):
        model_name = os.path.basename(model_dir)
        if model_name not in AVAILABLE_MODELS:
            raise NotImplementedError('%s is not an available downloaded model' % model_name)
        url = AVAILABLE_MODELS[model_name][1]
        download(url, path=zip_file_path, overwrite=True)
    with zipfile.ZipFile(zip_file_path) as zf:
        zf.extractall(par_dir)
    os.remove(zip_file_path)

    return model_dir


def read_charset(charset_fp):
    alphabet = []
    with open(charset_fp, encoding='utf-8') as fp:
        for line in fp:
            alphabet.append(line.rstrip('\n'))
    inv_alph_dict = {_char: idx for idx, _char in enumerate(alphabet)}
    if len(alphabet) != len(inv_alph_dict):
        from collections import Counter
        repeated = Counter(alphabet).most_common(len(alphabet) - len(inv_alph_dict))
        raise ValueError('repeated chars in vocab: %s' % repeated)

    return alphabet, inv_alph_dict


RGB_MEAN = np.array([122.67891434, 116.66876762, 104.00698793])


def normalize_img_array(img, dtype='float32'):
    """ rescale to [-1.0, 1.0]
    :param img: RGB style
    :param dtype: resulting dtype
    """

    img = img.astype(dtype)
    img -= RGB_MEAN
    img = img / 255.0
    # img -= np.array((0.485, 0.456, 0.406))
    # img /= np.array((0.229, 0.224, 0.225))
    return img


def restore_img(img):
    """

    Args:
        img: [H, W, C]

    Returns: [H, W, C]

    """
    img = img * 255 + RGB_MEAN
    return img.clip(0, 255).astype(np.uint8)


def imread(img_fp) -> np.ndarray:
    """
    返回RGB格式的numpy数组
    Args:
        img_fp:

    Returns:
        RGB format ndarray: [C, H, W]

    """
    im = cv2.imread(img_fp, cv2.IMREAD_COLOR).astype('float32')  # res: color BGR, shape: [H, W, C]
    if im is None:
        im = np.asarray(Image.open(img_fp).convert('RGB'))
    else:
        im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
    return im.transpose((2, 0, 1))


def imsave(image, fp, normalized=True):
    """

    Args:
        image: [H, W, C]
        fp:

    Returns:

    """
    if normalized:
        image = restore_img(image)
    if image.dtype != np.uint8:
        image = image.clip(0, 255).astype(np.uint8)
    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    cv2.imwrite(fp, image)


def load_model_params(model, param_fp, device='cpu'):
    checkpoint = torch.load(param_fp, map_location=device)
    state_dict = checkpoint['state_dict']
    if all([param_name.startswith('model.') for param_name in state_dict.keys()]):
        # 表示导入的模型是通过 PlTrainer 训练出的 WrapperLightningModule，对其进行转化
        state_dict = {}
        for k, v in checkpoint['state_dict'].items():
            state_dict[k.split('.', maxsplit=1)[1]] = v
    model.load_state_dict(state_dict)
    return model