from collections import Counter, defaultdict
from itertools import chain
from six import iteritems, itervalues, string_types

from . import builtin
from .. import path
from .file_types import generated_file
from .install import can_install
from ..build_inputs import build_input
from ..file_types import *
from ..iterutils import iterate, uniques
from ..objutils import objectify
from ..safe_str import literal, shell_literal
from ..shell import posix as pshell
from ..shell.syntax import Syntax, Writer
from ..tools.pkg_config import PkgConfigPackage
from ..versioning import simplify_specifiers, SpecifierSet

build_input('pkg_config')(lambda build_inputs, env: [])


class Requirement(object):
    def __init__(self, name, version=None):
        self.name = name
        self.version = objectify(version or '', SpecifierSet)

    def merge(self, req):
        self.version = self.version & req.version

    def split(self, single=False):
        specs = simplify_specifiers(self.version)
        if len(specs) == 0:
            return [SimpleRequirement(self.name)]
        if single and len(specs) > 1:
            raise ValueError(
                ("multiple specifiers ({}) used in pkg-config requirement " +
                 "for '{}'").format(self.version, self.name)
            )
        return [SimpleRequirement(self.name, i) for i in specs]

    def __repr__(self):
        return '<Requirement({!r}, {!r})>'.format(
            self.name, str(self.version)
        )


class SimpleRequirement(object):
    def __init__(self, name, version=None):
        self.name = name
        self.version = version

    def _safe_str(self):
        if not self.version:
            return shell_literal(self.name)
        op = self.version.operator
        if op == '==':
            op = '='
        return shell_literal('{name} {op} {version}'.format(
            name=self.name, op=op, version=self.version.version
        ))

    def __repr__(self):
        return '<SimpleRequirement({!r}, {!r})>'.format(
            self.name, str(self.version)
        )


class RequirementSet(object):
    def __init__(self, iterable=None):
        self._reqs = {}
        if iterable:
            for i in iterable:
                self.add(i)

    def add(self, item):
        if item.name not in self._reqs:
            self._reqs[item.name] = item
        else:
            self._reqs[item.name].merge(item)

    def remove(self, item):
        del self._reqs[item.name]

    def update(self, other):
        for i in other:
            self.add(i)

    def merge_into(self, other):
        items = list(other)
        for i in items:
            if i.name in self._reqs:
                self._reqs[i.name].merge(i)
                other.remove(i)

    def split(self, single=False):
        return sorted(sum((i.split(single) for i in self), []),
                      key=lambda x: x.name)

    def __iter__(self):
        return itervalues(self._reqs)


class PkgConfigInfo(object):
    directory = path.Path('pkgconfig')

    class _simple_property(object):
        def __init__(self, fn):
            self.fn = fn

        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            return getattr(obj, '_' + self.fn.__name__)

        def __set__(self, obj, value):
            setattr(obj, '_' + self.fn.__name__, self.fn(obj, value))

    def __init__(self, builtins, name=None, desc_name=None, desc=None,
                 url=None, version=None, requires=None, requires_private=None,
                 conflicts=None, includes=None, libs=None, libs_private=None,
                 options=None, link_options=None, link_options_private=None,
                 lang='c', auto_fill=True):
        self._builtins = builtins
        self.auto_fill = auto_fill

        self.name = name
        self.desc_name = desc_name
        self.desc = desc
        self.url = url
        self.version = version
        self.lang = lang

        self.requires = requires
        self.requires_private = requires_private
        self.conflicts = conflicts

        self.includes = includes
        self.libs = libs
        self.libs_private = libs_private
        self.options = pshell.listify(options)
        self.link_options = pshell.listify(link_options)
        self.link_options_private = pshell.listify(link_options_private)

    @property
    def output(self):
        return PkgConfigPcFile(self.directory.append(self.name + '.pc'))

    @_simple_property
    def includes(self, value):
        return uniques(self._builtins['header_directory'](i)
                       for i in iterate(value)) if value is not None else None

    @_simple_property
    def libs(self, value):
        return (uniques(self._library(i) for i in iterate(value))
                if value is not None else None)

    @_simple_property
    def libs_private(self, value):
        return (uniques(self._library(i) for i in iterate(value))
                if value is not None else None)

    @_simple_property
    def requires(self, value):
        return (self._filter_packages(iterate(value))
                if value is not None else None)

    @_simple_property
    def requires_private(self, value):
        return (self._filter_packages(iterate(value))
                if value is not None else None)

    @_simple_property
    def conflicts(self, value):
        return (self._filter_packages(iterate(value))[0]
                if value is not None else None)

    def _library(self, lib):
        if isinstance(lib, DualUseLibrary):
            return lib
        return self._builtins['library'](lib)

    def _write_variable(self, out, name, value):
        out.write(name, Syntax.variable)
        out.write_literal('=')
        out.write(value, Syntax.variable)
        out.write_literal('\n')

    def _write_field(self, out, name, value, syntax=Syntax.variable, **kwargs):
        if value:
            out.write(name, Syntax.variable)
            out.write_literal(': ')
            out.write_each(iterate(value), syntax, **kwargs)
            out.write_literal('\n')

    def write(self, out, env):
        data = self._process_inputs()
        out = Writer(out)

        pkg = CommonPackage(
            None, None,
            includes=[installify(i, destdir=False) for i in data['includes']],
            libs=[installify(i.all[0], destdir=False) for i in data['libs']]
        )
        pkg_private = CommonPackage(
            None, None,
            libs=[installify(i.all[0], destdir=False)
                  for i in data['libs_private']]
        )

        builder = env.builder(self.lang)
        cflags = pkg.cflags(builder.compiler, None)

        linker = builder.linker('executable')
        ldflags = pkg.ldflags(linker, None) + pkg.ldlibs(linker, None)
        ldflags_private = (pkg_private.ldflags(linker, None) +
                           pkg_private.ldlibs(linker, None))

        for i in path.InstallRoot:
            if i != path.InstallRoot.bindir:
                self._write_variable(out, i.name, env.install_dirs[i])

        out.write_literal('\n')

        self._write_field(out, 'Name', data['desc_name'])
        self._write_field(out, 'Description', data['desc'])
        self._write_field(out, 'URL', data['url'])
        self._write_field(out, 'Version', data['version'])
        self._write_field(out, 'Requires', data['requires'], Syntax.shell,
                          delim=literal(', '))
        self._write_field(out, 'Requires.private', data['requires_private'],
                          Syntax.shell, delim=literal(', '))
        self._write_field(out, 'Conflicts', data['conflicts'],
                          Syntax.shell, delim=literal(', '))
        self._write_field(out, 'Cflags', cflags + data['cflags'], Syntax.shell)
        self._write_field(out, 'Libs', ldflags + data['ldflags'], Syntax.shell)
        self._write_field(out, 'Libs.private', ldflags_private +
                          data['ldflags_private'], Syntax.shell)

    def _process_inputs(self):
        result = {
            'name': self.name,
            'desc_name': self.desc_name or self.name,
            'url': self.url,
            'version': self.version,
        }
        result['desc'] = self.desc or '{} library'.format(result['desc_name'])

        includes = self.includes or []
        libs = self.libs or []
        libs_private = self.libs_private or []
        requires, extra = self.requires or [RequirementSet(), []]
        requires_private, extra_private = (self.requires_private or
                                           [RequirementSet(), []])
        conflicts = self.conflicts or RequirementSet()

        fwd_ldflags = sum(
            (i.forward_args['options'] if hasattr(i, 'forward_args') else []
             for i in chain(libs, libs_private)), []
        )

        # Add all the (unique) dependent libs to libs_private, unless they're
        # already in libs.
        libs_private = uniques(chain(
            chain.from_iterable(
                i.forward_args['libs'] if hasattr(i, 'forward_args') else []
                for i in chain(libs, libs_private) if i not in libs
            ), libs_private
        ))

        # Get the package dependencies for all the libs (public and private)
        # that were passed in.
        auto_requires, auto_extra = self._filter_packages(chain.from_iterable(
            i.package_deps for i in chain(libs, libs_private)
        ))

        requires_private.update(auto_requires)
        requires.merge_into(requires_private)

        result['requires'] = requires.split(single=True)
        result['requires_private'] = requires_private.split(single=True)
        result['conflicts'] = conflicts.split()

        # Add all the options from each of the system packages (.includes,
        # .libs, and occasionally .lib_dirs).
        def process_packages(pkgs, private=False):
            def name(x):
                return x + '_private' if private else x

            core = (name('includes'), name('libs'))
            extra = name('extra_fields')
            for i in pkgs:
                for k, v in iteritems(i.all_options):
                    if isiterable(v):
                        (result if k in core else result[extra])[k].extend(v)

        result['includes'] = includes
        result['libs'] = libs
        result['libs_private'] = libs_private
        result['extra_fields'] = defaultdict(list)

        process_packages(extra)
        process_packages(chain(extra_private, auto_extra), private=True)

        result['cflags'] = self.options
        result['ldflags'] = self.link_options
        result['ldflags_private'] = fwd_ldflags + self.link_options_private

        return result

    @staticmethod
    def _filter_packages(packages):
        pkg_config = RequirementSet()
        system = []
        for i in packages:
            if isinstance(i, string_types):
                pkg_config.add(Requirement(i))
            elif isinstance(i, (tuple, list)):
                pkg_config.add(Requirement(*i))
            elif isinstance(i, PkgConfigPackage):
                pkg_config.add(Requirement(i.name, i.specifier))
            elif isinstance(i, CommonPackage):
                system.append(i)
            else:
                raise TypeError('unsupported package type: {}'.format(type(i)))
        return pkg_config, uniques(system)


@builtin.globals('builtins', 'build_inputs', 'env')
def pkg_config(builtins, build, env, name=None, **kwargs):
    if can_install(env):
        build['pkg_config'].append(PkgConfigInfo(builtins, name, **kwargs))


@builtin.post('builtins', 'build_inputs', 'env')
def finalize_pkg_config(builtins, build, env):
    install = build['install']
    defaults = {
        'name': build['project'].name,
        'version': build['project'].version or '0.0',
        'includes': [i for i in install
                     if isinstance(i, (HeaderFile, HeaderDirectory))],
        'libs': uniques(getattr(i, 'parent', i) for i in install
                        if isinstance(i, Library)),
    }

    for info in build['pkg_config']:
        if not info.auto_fill:
            continue
        for key, value in iteritems(defaults):
            if getattr(info, key) is None:
                setattr(info, key, value)

    # Make sure we don't have any duplicate pkg-config packages.
    dupes = Counter(i.name for i in build['pkg_config'])
    for name, count in iteritems(dupes):
        if count > 1:
            raise ValueError("duplicate pkg-config package '{}'".format(name))

    for info in build['pkg_config']:
        with generated_file(build, env, info.output) as out:
            info.write(out, env)
            builtins['install'](info.output)
