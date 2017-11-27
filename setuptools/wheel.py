'''Wheels support.'''

from distutils.util import get_platform
import email
import itertools
import os
import re
import zipfile

from pkg_resources import Distribution, PathMetadata, parse_version
from pkg_resources.extern.six import PY3
from setuptools import Distribution as SetuptoolsDistribution
from setuptools import pep425tags
from setuptools.command.egg_info import write_requirements


WHEEL_NAME = re.compile(
    r"""^(?P<project_name>.+?)-(?P<version>\d.*?)
    ((-(?P<build>\d.*?))?-(?P<py_version>.+?)-(?P<abi>.+?)-(?P<platform>.+?)
    )\.whl$""",
re.VERBOSE).match

NAMESPACE_PACKAGE_INIT = '''\
try:
    __import__('pkg_resources').declare_namespace(__name__)
except ImportError:
    __path__ = __import__('pkgutil').extend_path(__path__, __name__)
'''


class Wheel(object):

    def __init__(self, filename):
        match = WHEEL_NAME(os.path.basename(filename))
        if match is None:
            raise ValueError('invalid wheel name: %r' % filename)
        self.filename = filename
        for k, v in match.groupdict().items():
            setattr(self, k, v)

    def tags(self):
        '''List tags (py_version, abi, platform) supported by this wheel.'''
        return itertools.product(self.py_version.split('.'),
                                 self.abi.split('.'),
                                 self.platform.split('.'))

    def is_compatible(self):
        '''Is the wheel is compatible with the current platform?'''
        supported_tags = pep425tags.get_supported()
        return next((True for t in self.tags() if t in supported_tags), False)

    def egg_name(self):
        return Distribution(
            project_name=self.project_name, version=self.version,
            platform=(None if self.platform == 'any' else get_platform()),
        ).egg_name() + '.egg'

    def install_as_egg(self, destination_eggdir):
        '''Install wheel as an egg directory.'''
        with zipfile.ZipFile(self.filename) as zf:
            dist_basename = '%s-%s' % (self.project_name, self.version)
            dist_info = '%s.dist-info' % dist_basename
            dist_data = '%s.data' % dist_basename
            def get_metadata(name):
                with zf.open('%s/%s' % (dist_info, name)) as fp:
                    value = fp.read().decode('utf-8') if PY3 else fp.read()
                    return email.parser.Parser().parsestr(value)
            wheel_metadata = get_metadata('WHEEL')
            dist_metadata = get_metadata('METADATA')
            # Check wheel format version is supported.
            wheel_version = parse_version(wheel_metadata.get('Wheel-Version'))
            if not parse_version('1.0') <= wheel_version < parse_version('2.0dev0'):
                raise ValueError('unsupported wheel format version: %s' % wheel_version)
            # Extract to target directory.
            os.mkdir(destination_eggdir)
            zf.extractall(destination_eggdir)
            # Convert metadata.
            dist_info = os.path.join(destination_eggdir, dist_info)
            dist = Distribution.from_location(
                destination_eggdir, dist_info,
                metadata=PathMetadata(destination_eggdir, dist_info)
            )
            # Note: we need to evaluate and strip markers now,
            # as we can't easily convert back from the syntax:
            # foobar; "linux" in sys_platform and extra == 'test'
            def raw_req(req):
                req.marker = None
                return str(req)
            install_requires = list(sorted(map(raw_req, dist.requires())))
            extras_require = {
                extra: list(sorted(
                    req
                    for req in map(raw_req, dist.requires((extra,)))
                    if req not in install_requires
                ))
                for extra in dist.extras
            }
            egg_info = os.path.join(destination_eggdir, 'EGG-INFO')
            os.rename(dist_info, egg_info)
            os.rename(os.path.join(egg_info, 'METADATA'),
                      os.path.join(egg_info, 'PKG-INFO'))
            setup_dist = SetuptoolsDistribution(attrs=dict(
                install_requires=install_requires,
                extras_require=extras_require,
            ))
            write_requirements(setup_dist.get_command_obj('egg_info'),
                               None, os.path.join(egg_info, 'requires.txt'))
            # Move data entries to their correct location.
            dist_data = os.path.join(destination_eggdir, dist_data)
            dist_data_scripts = os.path.join(dist_data, 'scripts')
            if os.path.exists(dist_data_scripts):
                egg_info_scripts = os.path.join(destination_eggdir,
                                                'EGG-INFO', 'scripts')
                os.mkdir(egg_info_scripts)
                for entry in os.listdir(dist_data_scripts):
                    # Remove bytecode, as it's not properly handled
                    # during easy_install scripts install phase.
                    if entry.endswith('.pyc'):
                        os.unlink(os.path.join(dist_data_scripts, entry))
                    else:
                        os.rename(os.path.join(dist_data_scripts, entry),
                                  os.path.join(egg_info_scripts, entry))
                os.rmdir(dist_data_scripts)
            for subdir in filter(os.path.exists, (
                os.path.join(dist_data, d)
                for d in ('data', 'headers', 'purelib', 'platlib')
            )):
                for entry in os.listdir(subdir):
                    os.rename(os.path.join(subdir, entry),
                              os.path.join(destination_eggdir, entry))
                os.rmdir(subdir)
            if os.path.exists(dist_data):
                os.rmdir(dist_data)
            # Fix namespace packages.
            namespace_packages = os.path.join(egg_info, 'namespace_packages.txt')
            if os.path.exists(namespace_packages):
                with open(namespace_packages) as fp:
                    namespace_packages = fp.read().split()
                for mod in namespace_packages:
                    mod_dir = os.path.join(destination_eggdir, *mod.split('.'))
                    mod_init = os.path.join(mod_dir, '__init__.py')
                    if os.path.exists(mod_dir) and not os.path.exists(mod_init):
                        with open(mod_init, 'w') as fp:
                            fp.write(NAMESPACE_PACKAGE_INIT)