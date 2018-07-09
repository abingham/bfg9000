import os.path

from . import *

is_mingw = (env.host_platform.name == 'windows' and
            env.builder('c++').flavor == 'cc')


@skip_if(is_mingw, 'xfail on mingw')
class TestPackage(IntegrationTest):
    def __init__(self, *args, **kwargs):
        IntegrationTest.__init__(
            self, os.path.join(examples_dir, '04_package'),
            *args, **kwargs
        )

    def test_build(self):
        self.build()
        self.assertOutput([executable('program')], '')


@skip_if(is_mingw, 'xfail on mingw')
class TestSystemPackage(IntegrationTest):
    def __init__(self, *args, **kwargs):
        IntegrationTest.__init__(
            self, os.path.join(examples_dir, '04_package'),
            env={'PKG_CONFIG': 'nonexist'}, *args, **kwargs
        )

    def test_build(self):
        self.build()
        self.assertOutput([executable('program')], '')


@skip_if(is_mingw, 'xfail on mingw')
class TestBoostPackage(IntegrationTest):
    def __init__(self, *args, **kwargs):
        IntegrationTest.__init__(self, 'boost', *args, **kwargs)

    def test_build(self):
        self.build()
        self.assertOutput([executable('program'), '--hello'],
                          'Hello, world!\n')


@skip_if(is_mingw, 'xfail on mingw')
class TestOpenGLPackage(IntegrationTest):
    def __init__(self, *args, **kwargs):
        IntegrationTest.__init__(self, 'opengl', *args, **kwargs)

    def test_build(self):
        self.build()
        self.assertOutput([executable('program')], '')


@skip_if(is_mingw, 'xfail on mingw')
class TestOpenGLSystemPackage(IntegrationTest):
    def __init__(self, *args, **kwargs):
        IntegrationTest.__init__(
            self, 'opengl', env={'PKG_CONFIG': 'nonexist'}, *args, **kwargs
        )

    def test_build(self):
        self.build()
        self.assertOutput([executable('program')], '')
