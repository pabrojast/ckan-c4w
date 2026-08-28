# ``namespace_packages=['ckanext']`` in setup.py requires the pkg_resources
# style declaration here. With ``extend_path`` alone, ``pip install -e .``
# fails to resolve sibling ckanext.* distributions.
try:
    __import__('pkg_resources').declare_namespace(__name__)
except ImportError:
    from pkgutil import extend_path
    __path__ = extend_path(__path__, __name__)
