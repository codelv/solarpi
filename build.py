import re
import os
import shutil
import subprocess

def find_version():
    with open("solarpi/__init__.py") as f:
        for line in f:
            if m := re.search(r'version = [\'"](.+)["\']', line):
                return m.group(1)
    raise Exception("Could not find version in solarpi/__init__.py")

PROJECT_ROOT = os.path.dirname(__file__)
DIST_DIR = os.path.join(os.path.dirname(__file__), "dist")
BUILD_DIR = os.path.join(os.path.dirname(__file__), "build")

def main():
    version = find_version()
    pkg_name = f"solarpi-v{version}"
    pkg_dir = os.path.join(BUILD_DIR, pkg_name)
    if os.path.exists(pkg_dir):
        shutil.rmtree(pkg_dir)
    deb_dir = os.path.join(pkg_dir, 'DEBIAN')
    install_dir = os.path.join(pkg_dir, 'opt/solar-pi')
    nginx_sites_available_dir = os.path.join(pkg_dir, 'etc/nginx/sites-available')
    sysd_dir = os.path.join(pkg_dir, 'etc/systemd/system')

    # Copy debian folder
    shutil.copytree(os.path.join(PROJECT_ROOT, 'DEBIAN'), deb_dir, dirs_exist_ok=True)

    # Replace version in control file
    control_file = os.path.join(deb_dir, 'control')
    with open(control_file) as f:
        modified_file = f.read().replace("X.X.X", version)
    with open(control_file, 'w') as f:
        f.write(modified_file)

    postinst_file = os.path.join(deb_dir, 'postinst')
    os.chmod(postinst_file, 0o775)

    # Copy solarpi module
    shutil.copytree(os.path.join(PROJECT_ROOT, 'solarpi'), os.path.join(install_dir, 'solarpi'), dirs_exist_ok=True)
    # Remove pycache
    pycache_dir = os.path.join(install_dir, 'solarpi', '__pycache__')
    if os.path.exists(pycache_dir):
        shutil.rmtree(pycache_dir)

    # Copy systemd service files
    os.makedirs(sysd_dir)
    shutil.copy(os.path.join(PROJECT_ROOT, 'solarpi-monitor.service'), sysd_dir)
    shutil.copy(os.path.join(PROJECT_ROOT, 'solarpi-web.service'), sysd_dir)

    # Copy nginx site conf file
    os.makedirs(nginx_sites_available_dir)
    shutil.copy(os.path.join(PROJECT_ROOT, 'solarpi.conf'), nginx_sites_available_dir)

    # Build the package
    subprocess.check_output(["dpkg-deb", "--build", pkg_dir])
    # Copy to dist
    os.makedirs(DIST_DIR, exist_ok=True)
    output_pkg = os.path.join(DIST_DIR, f"{pkg_name}.deb")
    shutil.move(f'{pkg_dir}.deb', output_pkg)
    print(f"deb generated at {output_pkg}")
    print(subprocess.check_output(["dpkg-deb", "--info", output_pkg]).decode())


if __name__ == '__main__':
    main()
