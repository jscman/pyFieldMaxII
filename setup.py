from setuptools import setup

setup(
    name='pyFieldMaxII',
    version='0.1',
    packages=['fieldmax'],
    install_requirements = ['ctypes'],
    url='https://github.com/jscman/pyFieldMaxII',
    license='MIT License',
    author='Julian Scheuermann',
    author_email='jscheuermann@pm.me',
    description='A python wrapper for the FieldMax2Lib.dll'
)
