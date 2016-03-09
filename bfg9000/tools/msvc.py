import os.path
from itertools import chain

from .winargparse import ArgumentParser
from .utils import library_macro
from .. import shell
from ..file_types import *
from ..iterutils import intersect, iterate, uniques
from ..path import Path, Root


class MsvcCompiler(object):
    def __init__(self, env, lang, name, command, cflags):
        self.platform = env.platform
        self.lang = lang

        self.rule_name = self.command_var = name
        self.command = command

        self.global_args = ['/nologo'] + cflags

    @property
    def flavor(self):
        return 'msvc'

    @property
    def deps_flavor(self):
        return 'msvc'

    def __call__(self, cmd, input, output, deps=None, args=None):
        result = [cmd]
        result.extend(iterate(args))
        if deps:
            result.append('/showIncludes')
        result.extend(['/c', input])
        result.append('/Fo' + output)
        return result

    def output_file(self, name):
        return ObjectFile(Path(name + '.obj', Root.builddir), self.lang)

    def _include_dir(self, directory):
        return ['/I' + directory.path]

    def args(self, includes):
        return sum((self._include_dir(i) for i in includes), [])

    def link_args(self, name, mode, static_libs):
        args = []
        if mode in ['shared_library', 'static_library']:
            args.append('/D' + library_macro(name, mode))
        elif mode != 'executable':
            raise ValueError("unknown mode '{}'".format(mode))

        args.extend('/D' + library_macro(i, 'static_library')
                    for i in static_libs)
        return args

    def parse_args(self, args):
        parser = ArgumentParser()
        parser.add('/D', '-D', type=list, dest='defines')
        parser.add('/I', '-I', type=list, dest='includes')
        parser.add('/W', type=list, dest='warnings')
        parser.add('/nologo')

        result, other = parser.parse_known(args)

        # Determine the active warning state.
        warnings = result.pop('warnings')
        levels = ['0', '1', '2', '3', '4', 'all']
        level = next(intersect(reversed(warnings), levels), None)
        as_error = next(intersect(reversed(warnings), ['X', 'X-']), None)
        if as_error:
            as_error = (as_error == 'X')

        result['warnings'] = {'level': level, 'as_error': as_error}
        result['other'] = other
        return result


class MsvcLinker(object):
    def __init__(self, env, lang, name, command, ldflags, ldlibs):
        self.platform = env.platform
        self.lang = lang

        self.rule_name = self.command_var = 'link_' + name
        self.command = command
        self.link_var = 'ld'

        self.global_args = ['/nologo'] + ldflags
        self.global_libs = ldlibs

    @property
    def flavor(self):
        return 'msvc'

    def can_link(self, langs):
        allowed = ({
            'c'     : {'c'},
            'c++'   : {'c', 'c++'},
        })[self.lang]
        return allowed.issuperset(langs)

    def __call__(self, cmd, input, output, libs=None, args=None):
        result = [cmd]
        result.extend(iterate(args))
        result.extend(iterate(input))
        result.extend(iterate(libs))
        result.append('/OUT:' + output)
        return result

    @property
    def auto_link(self):
        return True

    @property
    def _always_args(self):
        return []

    def lib_dirs(self, libraries, extra_dirs=[]):
        dirs = uniques(chain(
            (i.path.parent() for i in iterate(libraries)),
            extra_dirs
        ))
        return ['/LIBPATH:' + i for i in dirs]

    def args(self, libraries, output):
        return self._always_args + self.lib_dirs(libraries)

    def parse_args(self, args):
        parser = ArgumentParser()
        parser.add('/DLL')
        parser.add('/IMPLIB', type=str, dest='implib')
        parser.add('/nologo')

        result, other = parser.parse_known(args)
        result['other'] = other
        return result

    def _link_lib(self, library):
        if isinstance(library, WholeArchive):
            raise ValueError('MSVC does not support whole-archives')
        return [library.path.basename()]

    def always_libs(self, primary):
        return []

    def libs(self, libraries):
        return sum((self._link_lib(i) for i in libraries), [])


class MsvcExecutableLinker(MsvcLinker):
    def output_file(self, name):
        path = Path(name + self.platform.executable_ext, Root.builddir)
        return Executable(path)


class MsvcSharedLibraryLinker(MsvcLinker):
    def output_file(self, name, version=None, soversion=None):
        dllname = Path(name + self.platform.shared_library_ext, Root.builddir)
        impname = Path(name + '.lib', Root.builddir)
        expname = Path(name + '.exp', Root.builddir)
        dll = DllLibrary(dllname, impname, expname)
        return [dll, dll.import_lib, dll.export_file]

    @property
    def _always_args(self):
        return ['/DLL']

    def _import_lib(self, library):
        return ['/IMPLIB:' + library[1].path]

    def args(self, libraries, output):
        return (MsvcLinker.args(self, libraries, output) +
                self._import_lib(output))


class MsvcStaticLinker(object):
    link_var = 'lib'

    def __init__(self, env, lang, name, command):
        self.platform = env.platform
        self.lang = lang

        self.rule_name = self.command_var = 'lib_' + name
        self.command = command

        self.global_args = shell.split(env.getvar('LIBFLAGS', ''))

    @property
    def flavor(self):
        return 'msvc'

    def can_link(self, langs):
        # XXX: Only return true if the object format matches what we expect.
        return True

    def __call__(self, cmd, input, output, args=None):
        result = [cmd]
        result.extend(iterate(args))
        result.extend(iterate(input))
        result.append('/OUT:' + output)
        return result

    def output_file(self, name, langs):
        return StaticLibrary(Path(name + '.lib', Root.builddir), langs)

    def parse_args(self, args):
        return {'other': args}


class MsvcPackageResolver(object):
    def __init__(self, env, lang):
        value = env.getvar('CPATH')
        user_include_dirs = value.split(os.pathsep) if value else []

        value = env.getvar('INCLUDE')
        system_include_dirs = value.split(os.pathsep) if value else []

        self.include_dirs = [i for i in uniques(chain(
            user_include_dirs, system_include_dirs, env.platform.include_dirs
        )) if os.path.exists(i)]

        value = env.getvar('LIB')
        system_lib_dirs = value.split(os.pathsep) if value else []

        value = env.getvar('LIBRARY_PATH')
        user_lib_dirs = value.split(os.pathsep) if value else []

        all_lib_dirs = ( os.path.abspath(i) for i in
                         chain(user_lib_dirs, system_lib_dirs) )
        self.lib_dirs = [i for i in uniques(chain(
            all_lib_dirs, env.platform.lib_dirs
        )) if os.path.exists(i)]

        self.lang = lang

    def header(self, name, search_dirs=None):
        if search_dirs is None:
            search_dirs = self.include_dirs

        for base in search_dirs:
            if os.path.exists(os.path.join(base, name)):
                return HeaderDirectory(Path(base, Root.absolute), system=True,
                                       external=True)

        raise ValueError("unable to find header '{}'".format(name))

    def library(self, name, kind='any', search_dirs=None):
        if search_dirs is None:
            search_dirs = self.lib_dirs
        libname = name + '.lib'

        for base in search_dirs:
            fullpath = os.path.join(base, libname)
            if os.path.exists(fullpath):
                # We don't actually know what kind of library this is. It could
                # be a static library or an import library (which we classify
                # as a kind of shared lib).
                return Library(Path(fullpath, Root.absolute),
                               external=True)
        raise ValueError("unable to find library '{}'".format(name))
