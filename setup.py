from setuptools import setup


setup(
    name='stream2chromecast',
    packages=['stream2chromecast'],
    version='0.6',
    description='Chromecast media streamer',
    author='rfigueroa',
    author_email='rfigueroaoficial@gmail.com',
    url='https://github.com/rfigueroa/stream2chromecast',
    download_url='https://github.com/rfigueroa/stream2chromecast/tarball/0.1',
    keywords=['Chromecast', 'streamer'],
    classifiers=[],
    entry_points={
        'console_scripts': ['stream2chromecast=stream2chromecast:run'],
    }
)
