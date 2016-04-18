from ..backends.make import writer as make
from ..backends.ninja import writer as ninja
from ..path import Path, Root


def _dist_command(backend, build_inputs, buildfile, env):
    srcdir = Path('.', Root.srcdir)
    tar = env.tool('tar')
    cmd = backend.cmd_var(tar, buildfile)

    project = build_inputs['project']
    if project.version:
        dstname = '{}-{}.tar.gz'.format(project.name, project.version)
    else:
        dstname = '{}.tar.gz'.format(project.name)
    return [tar(cmd, [i.path.relpath(srcdir) for i in build_inputs.sources],
                Path(dstname), srcdir)]


@make.post_rule
def make_dist_rule(build_inputs, buildfile, env):
    buildfile.rule(
        target='dist',
        recipe=_dist_command(make, build_inputs, buildfile, env),
        phony=True
    )


@ninja.post_rule
def ninja_dist_rule(build_inputs, buildfile, env):
    ninja.command_build(
        buildfile, env,
        output='dist',
        commands=_dist_command(ninja, build_inputs, buildfile, env)
    )