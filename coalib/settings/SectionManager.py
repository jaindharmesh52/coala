import copy
import os
import sys

from coalib.bears.BEAR_KIND import BEAR_KIND
from coalib.collecting.Collectors import collect_bears
from coalib.misc.StringConstants import StringConstants
from coalib.misc.i18n import _
from coalib.output.ConfWriter import ConfWriter
from coalib.output.NullInteractor import NullInteractor
from coalib.output.ClosableObject import ClosableObject
from coalib.output.printers.ConsolePrinter import ConsolePrinter
from coalib.output.printers.FilePrinter import FilePrinter
from coalib.output.printers.NullPrinter import NullPrinter
from coalib.output.ConsoleInteractor import ConsoleInteractor
from coalib.output.printers.LOG_LEVEL import LOG_LEVEL
from coalib.parsing.CliParser import CliParser
from coalib.parsing.ConfParser import ConfParser
from coalib.settings.Section import Section
from coalib.settings.SectionFiller import SectionFiller
from coalib.settings.Setting import path_list


def merge_section_dicts(lower, higher):
    """
    Merges the section dictionaries. The values of higher will take
    precedence over the ones of lower. Lower will hold the modified dict in
    the end.

    :param lower:  A section.
    :param higher: A section which values will take precedence over the ones
                   from the other.
    :return:       The merged dict.
    """
    for name in higher:
        if name in lower:
            lower[name].update(higher[name], ignore_defaults=True)
        else:
            # no deep copy needed
            lower[name] = higher[name]

    return lower


def load_config_file(filename, log_printer, silent=False):
    """
    Loads sections from a config file. Prints an appropriate warning if
    it doesn't exist and returns a section dict containing an empty
    default section in that case.

    It assumes that the cli_sections are available.

    :param filename:    The file to load settings from.
    :param log_printer: The log printer to log the warning to (in case).
    :param silent:      Whether or not to warn the user if the file doesn't
                        exist.
    """
    filename = os.path.abspath(filename)
    conf_parser = ConfParser()

    try:
        return conf_parser.reparse(filename)
    except conf_parser.FileNotFoundError:
        if not silent:
            log_printer.warn(
                _("The requested coafile '{filename}' does not exist. "
                  "Thus it will not be used.").format(filename=filename))

        return {"default": Section("default")}


def save_sections(sections):
    """
    Saves the given sections if they are to be saved.

    :param sections: A section dict.
    """
    default_section = sections["default"]
    try:
        if bool(default_section.get("save", "false")):
            conf_writer = ConfWriter(
                str(default_section.get("config", ".coafile")))
        else:
            return
    except ValueError:
        conf_writer = ConfWriter(str(default_section.get("save", ".coafile")))

    conf_writer.write_sections(sections)
    conf_writer.close()


def warn_nonexistent_targets(targets, sections, log_printer):
    """
    Prints out a warning on the given log printer for all targets that are
    not existent within the given sections.

    :param targets:     The targets to check.
    :param sections:    The sections to search. (Dict.)
    :param log_printer: The log printer to warn to.
    """
    for target in targets:
        if target not in sections:
            log_printer.warn(
                _("The requested section '{section}' is not existent. "
                  "Thus it cannot be executed.").format(section=target))


class SectionManager:
    """
    The SectionManager does the following things:

    - Reading all settings in sections from
        - Default config
        - CLI
        - Configuration file
    - Collecting all the bears
    - Filling up all needed settings
    - Write back the new sections to the configuration file if needed
    - Give all information back to caller

    This is done when the run() method is invoked. Anything else is just helper
    stuff and initialization.
    """
    def __init__(self):
        self.cli_sections = None
        self.default_sections = None
        self.user_sections = None
        self.coafile_sections = None
        self.sections = None

        self.cli_parser = CliParser()
        self.conf_parser = ConfParser()

        self.local_bears = {}
        self.global_bears = {}

        self.log_printer = None
        self.interactor = None

        self.targets = []

    def run(self, arg_list=sys.argv[1:]):
        """
        Loads all configuration files, retrieves bears and all needed
        settings, saves back if needed and warns about non-existent targets.

        :param arg_list: CLI args to use
        :return:         A tuple with the following contents:
                          * A dictionary with the sections
                          * Dictionary of list of local bears for each section
                          * Dictionary of list of global bears for each section
                          * The targets list
                          * The interactor (needs to be closed!)
                          * The log printer (needs to be closed!)
        """
        self._load_configuration(arg_list)
        self.retrieve_logging_objects(self.sections["default"])
        self._fill_settings()
        save_sections(self.sections)
        warn_nonexistent_targets(self.targets, self.sections, self.log_printer)

        return (self.sections,
                self.local_bears,
                self.global_bears,
                self.targets,
                self.interactor,
                self.log_printer)

    def _load_configuration(self, arg_list):
        self.cli_sections = self.cli_parser.reparse(arg_list=arg_list)
        self.retrieve_logging_objects(self.cli_sections["default"])
        # We dont want to store targets argument back to file, thus remove it
        for item in list(
                self.cli_sections["default"].contents.pop("targets", "")):
            self.targets.append(item.lower())

        self.default_sections = load_config_file(
            StringConstants.system_coafile,
            self.log_printer)

        self.user_sections = load_config_file(
            StringConstants.user_coafile,
            self.log_printer,
            silent=True)

        default_config = str(
            self.default_sections["default"].get("config", ".coafile"))
        user_config = str(
            self.user_sections["default"].get("config", default_config))
        config = os.path.abspath(str(
            self.cli_sections["default"].get("config", user_config)))

        self.coafile_sections = load_config_file(config, self.log_printer)

        self.sections = merge_section_dicts(self.default_sections,
                                            self.user_sections)

        self.sections = merge_section_dicts(self.sections,
                                            self.coafile_sections)

        self.sections = merge_section_dicts(self.sections,
                                            self.cli_sections)

        for section in self.sections:
            if section != "default":
                self.sections[section].defaults = self.sections["default"]

    def retrieve_logging_objects(self, section):
        """
        Creates an appropriate log printer and interactor according to the
        settings.
        """
        if self.interactor is not None and isinstance(self.interactor,
                                                      ClosableObject):
            # Cannot be tested - we dont have an Interactor needing closing yet
            self.interactor.close()  # pragma: no cover
        if self.log_printer is not None and isinstance(self.log_printer,
                                                       ClosableObject):
            self.log_printer.close()

        log_type = str(section.get("log_type", "console")).lower()
        output_type = str(section.get("output", "console")).lower()
        str_log_level = str(section.get("log_level", "")).upper()
        log_level = LOG_LEVEL.str_dict.get(str_log_level, LOG_LEVEL.WARNING)

        if log_type == "console":
            self.log_printer = ConsolePrinter(log_level=log_level)
        else:
            try:
                # ConsolePrinter is the only printer which may not throw an
                # exception (if we have no bugs though) so well fallback to him
                # if some other printer fails
                if log_type == "none":
                    self.log_printer = NullPrinter()
                else:
                    self.log_printer = FilePrinter(filename=log_type,
                                                   log_level=log_level)
            except:
                self.log_printer = ConsolePrinter(log_level=log_level)
                self.log_printer.log(
                    LOG_LEVEL.WARNING,
                    _("Failed to instantiate the logging method '{}'. Falling "
                      "back to console output.").format(log_type))

        if output_type == "none":
            self.interactor = NullInteractor(log_printer=self.log_printer)
        else:
            self.interactor = ConsoleInteractor.from_section(
                section,
                log_printer=self.log_printer)

    def _fill_settings(self):
        for section_name in self.sections:
            section = self.sections[section_name]

            bear_dirs = path_list(section.get("bear_dirs", ""))
            bear_dirs.append(os.path.join(StringConstants.coalib_bears_root,
                                          "**"))
            bears = list(section.get("bears", ""))
            local_bears = collect_bears(bear_dirs,
                                        bears,
                                        [BEAR_KIND.LOCAL],
                                        self.log_printer)
            global_bears = collect_bears(bear_dirs,
                                         bears,
                                         [BEAR_KIND.GLOBAL],
                                         self.log_printer)
            filler = SectionFiller(section, self.interactor, self.log_printer)
            all_bears = copy.deepcopy(local_bears)
            all_bears.extend(global_bears)
            filler.fill_section(all_bears)

            self.local_bears[section_name] = local_bears
            self.global_bears[section_name] = global_bears
