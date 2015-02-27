from setuptools import setup


setup(
    setup_requires=['pbr'],
    pbr=True,
    extras_require = {
        'rpc': ['oslo.messaging==1.7.0'],
    },
)
