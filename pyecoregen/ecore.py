"""Support for generation for models based on pyecore."""
import itertools
import os
import re

import multigen.formatter
import multigen.jinja
from pyecore import ecore
from pyecoregen.adapter import pythonic_names


class EcoreTask(multigen.jinja.JinjaTask):
    """
    Base class for Jinja based generation of Pyecore models.

    Attributes:
        element_type: Ecore type to be searched in model and to be iterated over.
    """

    element_type = None

    def filtered_elements(self, model):
        """Return iterator based on `element_type`."""
        if isinstance(model, self.element_type):
            yield model
        yield from (e for e in model.eAllContents() if isinstance(e, self.element_type))

    @classmethod
    def folder_path_for_package(cls, package: ecore.EPackage):
        """Returns path to folder holding generated artifact for given element."""
        parent = package.eContainer()
        if parent:
            return os.path.join(cls.folder_path_for_package(parent), package.name)
        return package.name

    @staticmethod
    def filename_for_element(package: ecore.EPackage):
        """Returns generated file name."""
        raise NotImplementedError

    def relative_path_for_element(self, element: ecore.EPackage):
        path = os.path.join(self.folder_path_for_package(element),
                            self.filename_for_element(element))
        return path


class EcorePackageInitTask(EcoreTask):
    """Generation of package init file from Ecore model with Jinja2."""

    template_name = 'package.py.tpl'
    element_type = ecore.EPackage

    @staticmethod
    def filename_for_element(package: ecore.EPackage):
        return '__init__.py'


class EcorePackageModuleTask(EcoreTask):
    """Generation of package model from Ecore model with Jinja2."""

    template_name = 'module.py.tpl'
    element_type = ecore.EPackage

    @staticmethod
    def imported_classifiers(p: ecore.EPackage):
        """Determines which classifiers have to be imported into given package."""
        classes = {c for c in p.eClassifiers if isinstance(c, ecore.EClass)}

        supertypes = itertools.chain(*(c.eAllSuperTypes() for c in classes))
        imported = {c for c in supertypes if c.ePackage is not p}

        attributes = itertools.chain(*(c.eAttributes for c in classes))
        attributes_types = (a.eType for a in attributes)
        enum_types = (t for t in attributes_types if isinstance(t, ecore.EEnum))
        imported |= {t for t in enum_types if t.ePackage is not p}

        # sort by owner package name:
        return sorted(imported, key=lambda c: c.ePackage.name)

    @staticmethod
    def classes(p: ecore.EPackage):
        """Returns classes in package in ordered by number of bases."""
        classes = (c for c in p.eClassifiers if isinstance(c, ecore.EClass))
        return sorted(classes, key=lambda c: len(set(c.eAllSuperTypes())))

    @staticmethod
    def filename_for_element(package: ecore.EPackage):
        return '{}.py'.format(package.name)

    def create_template_context(self, element, **kwargs):
        return super().create_template_context(
            element=element,
            classes=self.classes(element),
            imported_classifiers=self.imported_classifiers(element)
        )


class EcoreGenerator(multigen.jinja.JinjaGenerator):
    """Generation of static ecore model classes."""

    tasks = [
        EcorePackageInitTask(formatter=multigen.formatter.format_autopep8),
        EcorePackageModuleTask(formatter=multigen.formatter.format_autopep8),
    ]

    templates_path = os.path.join(
        os.path.abspath(os.path.dirname(__file__)),
        'templates'
    )

    def __init__(self, auto_register_package=False, **kwargs):
        self.auto_register_package = auto_register_package
        super().__init__(**kwargs)

    @staticmethod
    def test_type(value, type_):
        """Jinja test to check if an object's class is exactly the tested type."""
        return value.__class__ is type_

    @staticmethod
    def test_kind(value, type_):
        """Jinja test to check the 'kind' or an object.
        An object is 'kind' of a type when the object's class isinstance from the tested type.
        """
        return isinstance(value, type_)

    @staticmethod
    def test_opposite_before_self(value: ecore.EReference, references):
        try:
            return references.index(value.eOpposite) < references.index(value)
        except ValueError:
            return False

    @staticmethod
    def filter_docstringline(value: ecore.EModelElement) -> str:
        annotation = value.getEAnnotation('http://www.eclipse.org/emf/2002/GenModel')
        doc = annotation.details.get('documentation', '') if annotation else None
        return '"""{}"""'.format(doc) if doc else ''

    @staticmethod
    def filter_supertypes(value: ecore.EClass):
        supertypes = ', '.join(t.name for t in value.eSuperTypes)
        return supertypes if supertypes else 'EObject, metaclass=MetaEClass'

    @staticmethod
    def filter_pyquotesingle(value: str):
        return '\'{}\''.format(value) if value is not None else ''

    @staticmethod
    def filter_refqualifiers(value: ecore.EReference):
        qualifiers = dict(
            ordered=value.ordered,
            unique=value.unique,
            containment=value.containment,
        )
        if value.many:
            qualifiers.update(upper=-1)

        return ', '.join('{}={}'.format(k, v) for k, v in qualifiers.items())

    @staticmethod
    def filter_attrqualifiers(value: ecore.EAttribute):
        qualifiers = dict(
            eType=value.eType.name,
            derived=value.derived,
            changeable=value.changeable,
            iD=value.iD,
        )
        if value.many:
            qualifiers.update(upper=-1)
        if value.derived:
            qualifiers.update(name='{v.name!r}'.format(v=value))

        return ', '.join('{}={}'.format(k, v) for k, v in qualifiers.items())

    @staticmethod
    def filter_all_contents(value: ecore.EPackage, type_):
        """Returns `eAllContents(type_)`."""
        return (c for c in value.eAllContents() if isinstance(c, type_))

    @classmethod
    def filter_pyfqn(cls, value, relative_to=0):
        """
        Returns Python form of fully qualified name.

        Args:
            relative_to: If greater 0, the returned path is relative to the first n directories.
        """

        def collect_packages(element, packages):
            parent = element.eContainer()
            if parent:
                collect_packages(parent, packages)
            packages.append(element.name)

        packages = []
        collect_packages(value, packages)

        if relative_to < 0 or relative_to > len(packages):
            raise ValueError('relative_to not in range of number of packages')

        fqn = '.'.join(packages[relative_to:])

        if relative_to:
            fqn = '.' + fqn

        return fqn

    @staticmethod
    def filter_set(value):
        """Returns set of passed iterable."""
        return set(value)

    def create_global_context(self, **kwargs):
        return super().create_global_context(auto_register_package=self.auto_register_package)

    def create_environment(self, **kwargs):
        """
        Return a new Jinja environment.

        Derived classes may override method to pass additional parameters or to change the template
        loader type.
        """
        environment = super().create_environment(**kwargs)
        environment.tests.update({
            'type': self.test_type,
            'kind': self.test_kind,
            'opposite_before_self': self.test_opposite_before_self,
        })
        environment.filters.update({
            'docstringline': self.filter_docstringline,
            'pyquotesingle': self.filter_pyquotesingle,
            'refqualifiers': self.filter_refqualifiers,
            'attrqualifiers': self.filter_attrqualifiers,
            'supertypes': self.filter_supertypes,
            'all_contents': self.filter_all_contents,
            'pyfqn': self.filter_pyfqn,
            're_sub': lambda v, p, r: re.sub(p, r, v),
            'set': self.filter_set,
        })

        from pyecore import ecore
        environment.globals.update({'ecore': ecore})

        return environment

    def generate(self, model, outfolder):
        with pythonic_names():
            super().generate(model, outfolder)
