#!/usr/bin/python3

import os, sys, zipfile, re
import glob
import subprocess
from shutil import rmtree, which, move
from distutils.version import LooseVersion
import six
import urllib.request, urllib.parse, urllib.error
from sdk_prebuilts import maven_to_make as sdk_maven_to_make
from sdk_prebuilts import deps_rewrite as sdk_deps_rewrite

# Some code was kanged from `/prebuilts/sdk/update_prebuilts/update_prebuilts.py`

current_path = 'current'

temp_dir = os.path.join(os.getcwd(), "support_tmp")
os.chdir(os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[0]))))
git_dir = os.getcwd()

# Dictionary of maven repos
maven_repos = {
    'gmaven': {
        'name': 'GMaven',
        'url': 'https://maven.google.com'
    },
    'maven': {
        'name': 'Maven',
        'url': 'https://repo1.maven.org/maven2'
    }
}

# Dictionary of artifacts that will be updated
# artifacts pattern: 'group:library:version:extension': {}
# Use `latest` to always fetch the latest version.
# e.g.:
#   'androidx.appcompat:appcompat:latest:aar': {'repo': 'gmaven'}
maven_artifacts = {
    # databinding
    'androidx.databinding:databinding-runtime:latest:aar': {'repo': 'gmaven'},
    'androidx.databinding:databinding-common:latest:jar': {'repo': 'gmaven'},
    'androidx.databinding:databinding-adapters:latest:aar': {'repo': 'gmaven'},
    'androidx.databinding:databinding-ktx:latest:aar': {'repo': 'gmaven'},
    'androidx.databinding:viewbinding:latest:aar': {'repo': 'gmaven'},
}

# Mapping of POM dependencies to Soong build targets
dependencies_rewrite = {
}

def name_for_artifact(group_artifact):
    return group_artifact.replace(':','_')

def path_for_artifact(group_artifact):
    return group_artifact.replace('.','/').replace(':','/')

# build maven_to_make dict
maven_to_make = dict()

for k, v in maven_artifacts.items():
    (group, library, version, ext) = k.split(':')
    maven_to_make[':'.join([group, library])] = v

# Add automatic entries to maven_to_make.
for key in maven_to_make:
    if ('name' not in maven_to_make[key]):
        maven_to_make[key]['name'] = name_for_artifact(key)
    if ('path' not in maven_to_make[key]):
        maven_to_make[key]['path'] = path_for_artifact(key)

# Add dependencies rewrite rules from AOSP
sdk_deps_rewrite.update(dependencies_rewrite)
dependencies_rewrite = sdk_deps_rewrite

for key, val in sdk_maven_to_make.items():
    if key not in dependencies_rewrite:
        dependencies_rewrite[key] = name_for_artifact(key) if 'name' not in val else val['name']

# Always remove these files.
blacklist_files = [
    'annotations.zip',
    'public.txt',
    'R.txt',
    'AndroidManifest.xml',
    os.path.join('libs','noto-emoji-compat-java.jar')
]

artifact_pattern = re.compile(r"^(.+?)-(\d+\.\d+\.\d+(?:-\w+\d+)?(?:-[\d.]+)*)\.(jar|aar)$")

class MavenLibraryInfo:
    def __init__(self, key, group_id, artifact_id, version, dir, repo_dir, file):
        self.key = key
        self.group_id = group_id
        self.artifact_id = artifact_id
        self.version = version
        self.dir = dir
        self.repo_dir = repo_dir
        self.file = file


def print_e(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def rm(path):
    if os.path.isdir(path):
        rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def mv(src_path, dst_path):
    if os.path.exists(dst_path):
        rm(dst_path)
    if not os.path.exists(os.path.dirname(dst_path)):
        os.makedirs(os.path.dirname(dst_path))
    for f in (glob.glob(src_path)):
        if '*' in dst_path:
            dst = os.path.join(os.path.dirname(dst_path), os.path.basename(f))
        else:
            dst = dst_path
        move(f, dst)


def read_pom_file(path):
    # Read the POM (hack hack hack).
    group_id = ''
    artifact_id = ''
    version = ''
    with open(path) as pom_file:
        for line in pom_file:
            if line[:11] == '  <groupId>':
                group_id = line[11:-11]
            elif line[:14] == '  <artifactId>':
                artifact_id = line[14:-14]
            elif line[:11] == '  <version>':
                version = line[11:-11]

    return group_id, artifact_id, version


def detect_artifacts(maven_repo_dirs):
    maven_lib_info = {}

    # Find the latest revision for each artifact, remove others
    for repo_dir in maven_repo_dirs:
        for root, dirs, files in os.walk(repo_dir):
            for file in files:
                if file[-4:] == ".pom":
                    file = os.path.join(root, file)
                    group_id, artifact_id, version = read_pom_file(file)
                    if group_id == '' or artifact_id == '' or version == '':
                        print_e('Failed to find Maven artifact data in ' + file)
                        continue

                    # Locate the artifact.
                    artifact_file = file[:-4]
                    if os.path.exists(artifact_file + '.jar'):
                        artifact_file = artifact_file + '.jar'
                    elif os.path.exists(artifact_file + '.aar'):
                        artifact_file = artifact_file + '.aar'
                    else:
                        print_e('Failed to find artifact for ' + artifact_file)
                        continue

                    # Make relative to root.
                    artifact_file = artifact_file[len(root) + 1:]

                    # Find the mapping.
                    group_artifact = group_id + ':' + artifact_id
                    if group_artifact in maven_to_make:
                        key = group_artifact
                    elif artifact_id in maven_to_make:
                        key = artifact_id
                    else:
                        # No mapping entry, skip this library.
                        continue

                    # Store the latest version.
                    version = LooseVersion(version)
                    if key not in maven_lib_info \
                            or version > maven_lib_info[key].version:
                        maven_lib_info[key] = MavenLibraryInfo(key, group_id, artifact_id, version,
                                                               root, repo_dir, artifact_file)

    return maven_lib_info


def transform_maven_repos(maven_repo_dirs, transformed_dir, additional_artifacts = None, extract_res=True, include_static_deps=True):
    cwd = os.getcwd()

    # Use a temporary working directory.
    maven_lib_info = detect_artifacts(maven_repo_dirs)
    working_dir = temp_dir

    if not maven_lib_info:
        print_e('Failed to detect artifacts')
        return False

    for key, value in additional_artifacts.items():
        if key not in maven_lib_info:
            maven_lib_info[key] = value

    # extract some files (for example, AndroidManifest.xml) from any relevant artifacts
    for info in maven_lib_info.values():
        transform_maven_lib(working_dir, info, extract_res)

    # generate a single Android.bp that specifies to use all of the above artifacts
    makefile = os.path.join(working_dir, 'Android.bp')
    with open(makefile, 'w') as f:
        args = ["pom2bp"]
        args.extend(["-sdk-version", "31"])
        args.extend(["-default-min-sdk-version", "24"])
        if include_static_deps:
            args.append("-static-deps")
        rewriteNames = sorted([name for name in maven_to_make if ":" in name] + [name for name in maven_to_make if ":" not in name])
        args.extend(["-rewrite=^" + name + "$=" + maven_to_make[name]['name'] for name in rewriteNames])
        args.extend(["-rewrite=^" + key + "$=" + value for key, value in dependencies_rewrite.items()])
        args.extend(["-extra-static-libs=" + maven_to_make[name]['name'] + "=" + ",".join(sorted(maven_to_make[name]['extra-static-libs'])) for name in maven_to_make if 'extra-static-libs' in maven_to_make[name]])
        args.extend(["-optional-uses-libs=" + maven_to_make[name]['name'] + "=" + ",".join(sorted(maven_to_make[name]['optional-uses-libs'])) for name in maven_to_make if 'optional-uses-libs' in maven_to_make[name]])
        args.extend(["-host=" + name for name in maven_to_make if maven_to_make[name].get('host')])
        args.extend(["-host-and-device=" + name for name in maven_to_make if maven_to_make[name].get('host_and_device')])
        # these depend on GSON which is not in AOSP
        args.extend(["-exclude=android-arch-room-migration",
                     "-exclude=android-arch-room-testing"])
        args.extend(["."])
        subprocess.check_call(args, stdout=f, cwd=working_dir)

    # Replace the old directory.
    output_dir = os.path.join(cwd, transformed_dir)
    mv(working_dir, output_dir)
    return True

# moves <artifact_info> (of type MavenLibraryInfo) into the appropriate part of <working_dir> , and possibly unpacks any necessary included files
def transform_maven_lib(working_dir, artifact_info, extract_res):
    # Move library into working dir
    new_dir = os.path.normpath(os.path.join(working_dir, os.path.relpath(artifact_info.dir, artifact_info.repo_dir)))
    mv(artifact_info.dir, new_dir)

    matcher = artifact_pattern.match(artifact_info.file)
    maven_lib_name = artifact_info.key
    maven_lib_vers = matcher.group(2)
    maven_lib_type = artifact_info.file[-3:]

    group_artifact = artifact_info.key
    make_lib_name = maven_to_make[group_artifact]['name']
    make_dir_name = maven_to_make[group_artifact]['path']

    artifact_file = os.path.join(new_dir, artifact_info.file)

    if maven_lib_type == "aar":
        if extract_res:
            target_dir = os.path.join(working_dir, make_dir_name)
            if not os.path.exists(target_dir):
                os.makedirs(target_dir)

            process_aar(artifact_file, target_dir)

        with zipfile.ZipFile(artifact_file) as zip:
            manifests_dir = os.path.join(working_dir, "manifests")
            zip.extract("AndroidManifest.xml", os.path.join(manifests_dir, make_lib_name))

            databinding_class_info_dir = os.path.join(working_dir, 'data-binding', 'class-info', make_lib_name)
            databinding_artifacts_dir = os.path.join(working_dir, 'data-binding', 'artifacts', make_lib_name)
            for zip_info in zip.filelist:
                if zip_info.is_dir():
                    continue
                if zip_info.filename.startswith("data-binding-base-class-log/"):
                    zip.extract(zip_info, databinding_class_info_dir)
                elif zip_info.filename.startswith("data-binding/"):
                    zip.extract(zip_info, databinding_artifacts_dir)

    print(maven_lib_vers, ":", maven_lib_name, "->", make_lib_name)


def process_aar(artifact_file, target_dir):
    # Extract AAR file to target_dir.
    with zipfile.ZipFile(artifact_file) as zip:
        zip.extractall(target_dir)

    # Remove classes.jar
    classes_jar = os.path.join(target_dir, "classes.jar")
    if os.path.exists(classes_jar):
        os.remove(classes_jar)

    # Remove or preserve empty dirs.
    for root, dirs, files in os.walk(target_dir):
        for dir in dirs:
            dir_path = os.path.join(root, dir)
            if not os.listdir(dir_path):
                os.rmdir(dir_path)

    # Remove top-level cruft.
    for file in blacklist_files:
        file_path = os.path.join(target_dir, file)
        if os.path.exists(file_path):
            os.remove(file_path)


class MavenArtifact(object):
    # A map from group:library to the latest available version
    key_versions_map = {}

    def __init__(self, artifact_glob, repo_id):
        try:
            (group, library, version, ext) = artifact_glob.split(':')
        except ValueError:
            raise ValueError(f'Error in {artifact_glob} expected: group:library:version:ext')

        if not group or not library or not version or not ext:
            raise ValueError(f'Error in {artifact_glob} expected: group:library:version:ext')

        self.group = group
        self.group_path = group.replace('.', '/')
        self.library = library
        self.key = f'{group}:{library}'
        self.version = version
        self.ext = ext
        self.repo_id = repo_id
        self.repo_url = maven_repos[repo_id]['url']

    def get_pom_file_url(self):
        return f'{self.repo_url}/{self.group_path}/{self.library}/{self.version}/{self.library}-{self.version}.pom'

    def get_artifact_url(self):
        return f'{self.repo_url}/{self.group_path}/{self.library}/{self.version}/{self.library}-{self.version}.{self.ext}'

    def get_latest_version(self):
        latest_version = MavenArtifact.key_versions_map[self.key] \
                if self.key in MavenArtifact.key_versions_map else None

        if not latest_version:
            print(f'Fetching latest version for {self.key} ... ', end='')
            metadata_url = f'{self.repo_url}/{self.group_path}/{self.library}/maven-metadata.xml'
            import xml.etree.ElementTree as ET
            tree = ET.parse(urllib.request.urlopen(metadata_url))
            root = tree.getroot()
            latest_version = root.find('versioning').find('latest').text
            print(latest_version)
            MavenArtifact.key_versions_map[self.key] = latest_version

        return latest_version


def hack_pom_file(path, group_id):
    artifact_id_index = 0
    lines = []
    with open(path, 'r') as f:
        for line in f:
            if line.startswith('  <artifactId>'):
                lines.append(f'  <groupId>{group_id}</groupId>\n')
            elif line.startswith('  <groupId>'):
                continue
            lines.append(line)

    with open(path, 'w') as f:
        f.writelines(lines)


def fetch_maven_artifact(artifact):
    """Fetch a Maven artifact.

    Args:
        artifact_glob: an instance of MavenArtifact.
    """
    download_to = os.path.join(artifact.repo_id, artifact.group, artifact.library, artifact.version)

    pom_file_path = os.path.join(download_to, f'{artifact.library}-{artifact.version}.pom')
    _DownloadFileToDisk(artifact.get_pom_file_url(), pom_file_path)
    _DownloadFileToDisk(artifact.get_artifact_url(), os.path.join(download_to, f'{artifact.library}-{artifact.version}.{artifact.ext}'))

    group, library, version = read_pom_file(pom_file_path)

    if group != artifact.group:
        hack_pom_file(pom_file_path, artifact.group)

    return download_to


def _DownloadFileToDisk(url, filepath):
    """Download the file at URL to the location dictated by the path.

    Args:
        url: Remote URL to download file from.
        filepath: Filesystem path to write the file to.
    """
    print(f'Downloading URL: {url}')
    file_data = urllib.request.urlopen(url)

    try:
        os.makedirs(os.path.dirname(filepath))
    except os.error:
        # This is a common situation - os.makedirs fails if dir already exists.
        pass
    try:
        with open(filepath, 'wb') as f:
            f.write(six.ensure_binary(file_data.read()))
    except:
        os.remove(os.path.dirname(filepath))
        raise

def update_maven():
    artifacts = [MavenArtifact(key, value['repo']) for key, value in maven_artifacts.items()]
    for artifact in artifacts:
        if artifact.version == 'latest':
            artifact.version = artifact.get_latest_version()

    currents = detect_artifacts(['current'])

    need_updates = [artifact for artifact in artifacts \
        if artifact.key not in currents or artifact.version != str(currents[artifact.key].version)]

    if not need_updates:
        print_e('No any artifacts need to update')
        return []

    artifact_dirs = [fetch_maven_artifact(artifact) for artifact in need_updates]
    if not transform_maven_repos([repo for repo in maven_repos], current_path, currents, extract_res=False):
        return []

    return need_updates


def append(text, more_text):
    if text:
        return "%s, %s" % (text, more_text)
    return more_text


def uncommittedChangesExist():
    try:
        # Make sure we don't overwrite any pending changes.
        diffCommand = "cd " + git_dir + " && git diff --quiet"
        subprocess.check_call(diffCommand, shell=True)
        subprocess.check_call(diffCommand + " --cached", shell=True)
        return False
    except subprocess.CalledProcessError:
        return True


rm(temp_dir)

if which('pom2bp') is None:
    print_e("Cannot find pom2bp in path; please run lunch to set up build environment. You may also need to run 'm pom2bp' if it hasn't been built already.")
    sys.exit(1)

if uncommittedChangesExist():
    print_e('FAIL: There are uncommitted changes here. Please commit or stash before continuing, because %s will run "git reset --hard" if execution fails' % os.path.basename(__file__))
    sys.exit(1)

try:
    components = None

    msg = "Update prebuilt libraries\n\n"

    updated_artifacts = update_maven()
    if updated_artifacts:
        for artifact in updated_artifacts:
            msg += "Import %s %s from %s\n" % (artifact.key, artifact.version, maven_repos[artifact.repo_id]['name'])
    else:
        print_e('Failed to update artifacts, aborting...')
        sys.exit(1)

    subprocess.check_call(['git', 'add', current_path])
    subprocess.check_call(['git', 'commit', '-m', msg])

    print('Remember to test this change before uploading it to Gerrit!')

finally:
    # Revert all stray files, including the downloaded zip.
    try:
        with open(os.devnull, 'w') as bitbucket:
            subprocess.check_call(['git', 'add', '-Af', '.'], stdout=bitbucket)
            subprocess.check_call(
                ['git', 'commit', '-m', 'COMMIT TO REVERT - RESET ME!!!', '--allow-empty'], stdout=bitbucket)
            subprocess.check_call(['git', 'reset', '--hard', 'HEAD~1'], stdout=bitbucket)
    except subprocess.CalledProcessError:
        print_e('ERROR: Failed cleaning up, manual cleanup required!!!')
