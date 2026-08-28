# encoding: utf-8

from setuptools import setup, find_packages


setup(
    name='ckanext-c4w',
    version='0.1.0',
    description='Citizens4Water portal for CKAN (IHP-WINS)',
    long_description='',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'License :: OSI Approved :: GNU Affero General Public License v3',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
    keywords='CKAN UNESCO citizen-science water IHP-WINS citizens4water',
    author='UNESCO IHP-WINS',
    author_email='',
    url='https://github.com/pabrojast/ckanext-c4w',
    license='GNU Affero General Public License (AGPL) v3.0',
    packages=find_packages(exclude=['tests']),
    namespace_packages=['ckanext'],
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        # HTML sanitizer for user-supplied descriptions and blog bodies (see
        # ckanext/c4w/logic/sanitize.py). That module degrades fail-closed --
        # it strips every tag -- if bleach is somehow absent, but a portal
        # should never run in that mode, so declare it.
        'bleach',
        # Image validation and server-side cropping of the three project
        # images (logic/uploads.py). Reached today only transitively through
        # another extension; declared here so it never disappears silently.
        'Pillow',
        # Read-only client for the legacy Django database, used exclusively by
        # ckanext/c4w/migrate/source.py and imported *inside* that module so a
        # missing driver can never break CKAN startup.
        'psycopg2-binary',
    ],
    entry_points="""
        [ckan.plugins]
        c4w=ckanext.c4w.plugin:C4wPlugin
        [babel.extractors]
        ckan = ckan.lib.extract:extract_ckan
    """,
    message_extractors={
        'ckanext': [
            ('**.py', 'python', None),
            ('**.js', 'javascript', None),
            ('**/c4w/templates/**.html', 'ckan', None),
        ],
    },
)
