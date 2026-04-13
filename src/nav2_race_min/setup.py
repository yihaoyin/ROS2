from setuptools import setup

package_name = "nav2_race_min"

setup(
    name=package_name,
    version="0.0.1",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", ["launch/race_min.launch.py"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="dev",
    maintainer_email="dev@example.com",
    description="Minimal Lifecycle + Action race reproducer without full Navigation2 stack.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "mini_navigator_server = nav2_race_min.mini_navigator_server:main",
            "mini_race_stress = nav2_race_min.mini_race_stress:main",
        ],
    },
)
