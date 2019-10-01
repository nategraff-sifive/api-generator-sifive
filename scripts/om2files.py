#!/usr/bin/env python3.7

import json5
import json
import re
import sys
import typing as t
from pathlib import Path
import string

from dataclasses import dataclass

###
# JSON support
###

# type variable to handle our use of json
JsonType = t.TypeVar('JsonType', dict, list)

# Check if file exist before over-writing it
CHECK_EXISTENCE = True


@dataclass(frozen=True)
class Register:
    """
    Data class to hold register information
    """
    name: str
    offset: int  # in bytes
    width: int  # in bits


class InvalidOM(Exception):
    """
    Raised when an incorrect object is pass as something that should
    have been json
    """
    pass


def search_json_component(js_obj: JsonType, regex: re.Pattern) -> t.Generator:
    """
    Search a json object for any key or string matching regex. Return a
    generator for those objects.

    :param js_obj: A parsed javascript object
    :param regex: a compiled regular expression to search for.
    :return: A generator returning matching parts
    """
    if isinstance(js_obj, list):
        for i in js_obj:
            for j in search_json_component(i, regex):
                if j is not None:
                    yield j
    elif isinstance(js_obj, dict):
        sub_search = True
        for k, v in js_obj.items():
            if regex.match(k) or \
                    isinstance(v, str) and regex.match(v):
                yield js_obj
                sub_search = False
                break
        if sub_search:
            for d in search_json_component(list(js_obj.values()), regex):
                if d is not None:
                    yield d
    elif isinstance(js_obj, str):
        if regex.match(js_obj):
            yield js_obj


def find_component(js_obj: JsonType, regex_str: str) -> t.List[dict]:
    """

    :param js_obj: A dict or list from a parsed json file
    :param regex_str: A regular expression string to search js_obj for
    :return: a list of matching parts
    """
    regex = re.compile(regex_str)

    if isinstance(js_obj, list) or isinstance(js_obj, dict):
        return [i for i in search_json_component(js_obj, regex) if i is not None]
    else:
        raise InvalidOM


def search_json_field(js_obj: JsonType, field: str,
                      regex: re.Pattern) -> t.Generator:
    """

    :param js_obj: A dict or list from a parsed json file
    :param field: The name of the field to be found
    :param regex: A compiled regular expression to search values
    :return: a generator that will produce matching dicts.
    """
    def check_match(value: t.Union[list, str]):
        if isinstance(value, str):
            if regex.match(value):
                return True
        elif isinstance(value, list):
            for val in value:
                if isinstance(val, str) and regex.match(val):
                    return True
        return False

    if isinstance(js_obj, dict):
        if field in js_obj and check_match(js_obj[field]):
            yield js_obj
        else:
            for v in search_json_field(list(js_obj.values()), field, regex):
                if v is not None:
                    yield v
    elif isinstance(js_obj, list):
        for i in js_obj:
            for j in search_json_field(i, field, regex):
                if j is not None:
                    yield j


def find_json_field_name(js_obj: JsonType, field: str, regex_str: str):
    """
    Search the python representation of a Json Dict or list for a field
    whose value matches the given regex. Returns a list of matched dicts.
    The value will match a string, or any value in a list of strings.

    This function is a wrapper for search_json_field.

    :param js_obj: A dict or list from a parsed json file
    :param field: The name of the field to be found
    :param regex_str: regular expression to search values for
    :return: a list of matching dictionaries.
    """
    regex = re.compile(regex_str)

    if isinstance(js_obj, list) or isinstance(js_obj, dict):
        return [i for i in search_json_field(js_obj, field, regex)
                if i is not None]
    else:
        raise InvalidOM


###
# templates
###
def clean_str(a_str: str) -> str:
    """
    Clean up a multiline string so that the line begins after the '|'
    character. Useful for guaranteeing exact format of a long string.

    example:
    a = '''
    |this is
    |  that is
    |
    | foo
    '''

    clean_str(a) => 'this is\n  that is\n\n foo'
    """
    def post_pipe(b_str: str):
        return b_str.split('|', 1)[-1]
    return "\n".join(post_pipe(l).rstrip() for l in a_str.splitlines())


def generate_vtable(device_name: str, reg_list: t.List[Register]) -> str:
    """
    Generate the vtable entries for a device and set of register.
    :param device_name: the name of the device
    :param reg_list: a list of Register objects for the device
    :return: thh c code for the vtable entries
    """

    rv = []

    for a_reg in reg_list:
        reg_name = a_reg.name.lower()
        size = a_reg.width
        if size not in (8, 16, 32, 64):
            raise Exception('weird size')
        write_func = f'    void (*v_{device_name}_{reg_name}_write)' \
                     f'(uint32_t * {device_name}_base, uint{size}_t data);'
        read_func = f'    uint{size}_t (*v_{device_name}_{reg_name}_read)' \
                    f'(uint32_t  *{device_name}_base);'

        rv.append(write_func)
        rv.append(read_func)

    return '\n'.join(rv)


def generate_metal_dev(devices_name: str) -> str:
    """
    Generate the metal device for the given device name

    :param devices_name:
    :return: The c code for the metal device
    """

    return f'    uint32_t *{devices_name}_base;\n' + \
           f'    const struct metal_{devices_name}_vtable vtable;'


def generate_protos(device_name: str, reg_list: t.List[Register]):
    """
    Generate the function prototypes for a given device and register list.

    :param device_name: The device name
    :param reg_list: the list of registers for the device
    :return: the c language prototypes for the device
    """

    rv = []

    dev_struct = f'const struct metal_{device_name} *{device_name}'

    for a_reg in reg_list:
        reg_name = a_reg.name.lower()
        size = a_reg.width
        if size not in (8, 16, 32, 64):
            raise Exception('Non-standard size')

        write_func = f'void metal_{device_name}_{reg_name}_write' \
                     f'({dev_struct}, uint{size}_t data);'
        read_func = f'uint{size}_t metal_{device_name}_{reg_name}_read' \
                    f'({dev_struct});'
        rv.append(write_func)
        rv.append(read_func)

    get_device = f'const struct metal_{device_name} *get_metal_{device_name}' \
                 f'(uint8_t index);'
    rv.append(get_device)

    return '\n'.join(rv)


###
# templates
###

# The template for the .h file

METAL_DEV_HDR_TMPL = \
    """
    |#include <metal/compiler.h>
    |#include <stdint.h>
    |#include <stdlib.h>
    |
    |#ifndef ${company}_${dev}${index}_h
    |#define ${company}_${dev}${index}_h
    |#define ${cap_dev}_BASE ${base_address}
    |
    |struct metal_${dev};
    |
    |struct metal_${dev}_vtable {
    |${vtable}
    |};
    |
    |struct metal_${dev} {
    |${metal_dev}
    |};
    |
    |__METAL_DECLARE_VTABLE(metal_${dev})
    |
    |${protos}
    |#endif
    """


def generate_metal_dev_hdr(company, dev, index, base_address, reglist):
    """

    :param company: The name of the company creating the device
    :param dev: the name of the device created.
    :param index: the index of the device
    :param base_address: the base memory address of the device
    :param reglist: the list of registers for the device
    :return: a string which is the .h for file the device driver
    """
    template = string.Template(clean_str(METAL_DEV_HDR_TMPL))

    arg_dict = {'company': company,
                'dev': dev,
                'cap_dev': dev.upper(),
                'index': str(index),
                'base_address': hex(base_address),
                'vtable': generate_vtable(dev, reglist),
                'metal_dev': generate_metal_dev(dev),
                'protos': generate_protos(dev, reglist)}

    return template.substitute(**arg_dict)


# the template for the driver .c file
METAL_DEV_DRV_TMPL = \
    """
    |#include <stdint.h>
    |#include <stdlib.h>
    |
    |#include <${dev}/${company}_${dev}${index}.h>
    |#include <metal/compiler.h>
    |#include <metal/io.h>
    |
    |// Note: these macros have control_base as a hidden input
    |#define METAL_${cap_dev}_REG(offset) (((unsigned long)control_base + offset))
    |#define METAL_${cap_dev}_REGW(offset) \\
    |    (__METAL_ACCESS_ONCE((__metal_io_u32 *)METAL_${cap_dev}_REG(offset)))
    |
    |${offsets}
    |
    |${base_functions}
    |
    |${metal_functions}
    |
    |__METAL_DEFINE_VTABLE(metal_${dev}) = {
    |${def_vtable}
    |};
    |
    |const struct metal_${dev}* ${dev}_tables[] = {&metal_${dev}};
    |uint8_t ${dev}_tables_cnt = 1;
    |
    |const struct metal_${dev}* get_metal_${dev}(uint8_t idx)
    |{
    |    if (idx >= ${dev}_tables_cnt)
    |        return NULL;
    |    return ${dev}_tables[idx];
    |}
    """


def generate_def_vtable(dev: str, reg_list: t.List[Register]) -> str:
    """
    Generate vtable settings for vtable declaration in .c file

    :param dev: the name of the device
    :param reg_list: the register list for the device
    :return: the declarations in the vtable for the driver .c file
    """
    rv: t.List[str] = []
    cap_dev: str = dev.upper()
    head = f'    .{dev}_base = (uint32_t *){cap_dev}_BASE,'
    rv.append(head)
    for a_reg in reg_list:
        reg_name = a_reg.name.lower()

        write_func = f'    .vtable.v_{dev}_{reg_name}_write ' \
                     f'= {dev}_{reg_name}_write,'
        read_func = f'    .vtable.v_{dev}_{reg_name}_read ' \
                    f'= {dev}_{reg_name}_read,'
        rv.append(write_func)
        rv.append(read_func)

    return '\n'.join(rv)


def generate_offsets(device_name: str, reg_list: t.List[Register]) -> str:
    """
    Generate the register offset macros

    :param device_name: the name of the device
    :param reg_list: the list of registers for the device.
    :return:The offset c macros for the device and registers
    """
    rv: t.List[str] = []

    cap_dev = device_name.upper()
    for a_reg in reg_list:
        name = a_reg.name.upper()
        offset = a_reg.offset
        macro_line = f'#define METAL_{cap_dev}_{name} {offset}'
        rv.append(macro_line)

    return '\n'.join(rv)


def generate_base_functions(dev: str, reg_list: t.List[Register]) -> str:
    """
    Generates the basic, not exported register access functions for
    a given device and register list.

    :param dev: the name of the device
    :param reg_list: the list of registers for the device.
    :return:  the c code for the register access functions
    """
    cap_dev = dev.upper()
    rv: t.List[str] = []

    for a_reg in reg_list:
        name = a_reg.name.lower()
        cap_name = a_reg.name.upper()
        size = a_reg.width
        if size not in (8, 16, 32, 64):
            raise Exception('Non-standard size')

        write_func = f"""
            |void {dev}_{name}_write(uint32_t *{dev}_base, uint{size}_t data)
            |{{
            |    volatile uint32_t *control_base = {dev}_base;
            |    METAL_{cap_dev}_REGW(METAL_{cap_dev}_{cap_name}) = data;
            |}}
            """

        rv.append(clean_str(write_func))

        read_func = f"""
            |uint{size}_t {dev}_{name}_read(uint32_t *{dev}_base)
            |{{
            |    volatile uint32_t *control_base = {dev}_base;
            |    return METAL_{cap_dev}_REGW(METAL_{cap_dev}_{cap_name});
            |}}
            """

        rv.append(clean_str(read_func))

    return '\n'.join(rv)


def generate_metal_function(dev: str, reg_list: t.List[Register]) -> str:
    """
    Generates the exported register access functions for
    a given device and register list.

    :param dev: the name of the device
    :param reg_list: the list of registers for the device.
    :return:  the c code for the exported register access functions
    """

    rv: t.List[str] = []

    for a_reg in reg_list:
        name = a_reg.name.lower()
        size = a_reg.width
        if size not in (8, 16, 32, 64):
            raise Exception('Non-standard size')
        write_func = f"""
            |void metal_{dev}_{name}_write(const struct metal_{dev} *{dev}, uint{size}_t data)
            |{{
            |    if ({dev} != NULL)
            |        {dev}->vtable.v_{dev}_{name}_write({dev}->{dev}_base, data);
            |}}
            """
        rv.append(clean_str(write_func))

        read_func = f"""
            |uint{size}_t metal_{dev}_{name}_read(const struct metal_{dev} *{dev})
            |{{
            |    if ({dev} != NULL)
            |        return {dev}->vtable.v_{dev}_{name}_read({dev}->{dev}_base);
            |    return (uint{size}_t)-1;
            |}}
            """

        rv.append(clean_str(read_func))

    return '\n'.join(rv)


def generate_metal_dev_drv(company, dev, index, reglist):
    """
    Generate the driver source file contents for a given device
    and register list

    :param company: the company creating the device
    :param dev: the device
    :param index: the index of the device used
    :param reglist: the list of registers
    :return: a string containing of the c code for the basic driver
    """
    template = string.Template(clean_str(METAL_DEV_DRV_TMPL))

    arg_dict = {'company': company,
                'dev': dev,
                'cap_dev': dev.upper(),
                'index': str(index),
                'offsets': generate_offsets(dev, reglist),
                'base_functions': generate_base_functions(dev, reglist),
                'metal_functions': generate_metal_function(dev, reglist),
                'def_vtable': generate_def_vtable(dev, reglist)}

    return template.substitute(**arg_dict)


###
# main
###


def main():
    """

    :return: exits 0 on success, 1 on failure
    """
    print(sys.argv, file=sys.stderr)

    if len(sys.argv) < 5:
        def clear_arg_str(long_str):
            return '\n'.join(line.strip() for line in long_str.splitlines() if line)

        usage = f"""
        {sys.argv[0]} <object model file> \
        <company name> <device name> <metal directory>
        """

        print("Not enough arguments", file=sys.stderr)
        print(f'{clear_arg_str(usage)}', file=sys.stderr)
        sys.exit(1)

    object_model = json.load(open(sys.argv[1]))
    duh_info = json5.load(open(sys.argv[2]))
    company = sys.argv[3]
    device = sys.argv[4]
    metal_dir = sys.argv[5]

    addr_blocks = find_component(duh_info, 'addressBlocks')[0]['addressBlocks'][0]
    regs = addr_blocks['registers']

    reglist: t.List[Register] = []
    for a_reg in regs:
        name: str = a_reg['name']
        offset: int = a_reg['addressOffset'] // 8
        width: int = a_reg['size']

        reglist.append(Register(name, offset, width))

    memory_regions = find_json_field_name(object_model, '_types', 'OMMemoryRegion')
    devices = find_json_field_name(memory_regions, 'name', f'{device}.*')

    devices_enumerated = list(enumerate(devices))

    m_dir_path = Path(metal_dir)
    m_hdr_path = m_dir_path / device

    if not m_hdr_path.exists():
        m_hdr_path.mkdir()

    once: bool = True

    for i, om in devices_enumerated:
        index = i
        base = om['addressSets'][0]['base']

        if once is True:
            once = False
            driver_file_path = m_dir_path / f'{company}_{device}.c'
            if not driver_file_path.exists() and not CHECK_EXISTENCE:
                driver_file_path.write_text(
                    generate_metal_dev_drv(company, device, index, reglist))
            else:
                print(f"{str(driver_file_path)} exists, not creating.",
                      file=sys.stderr)

        header_file_path = m_hdr_path / f'{company}_{device}{index}.h'
        if not header_file_path.exists() and not CHECK_EXISTENCE:
            header_file_path.write_text(
                generate_metal_dev_hdr(company, device, index, base, reglist))
        else:
            print(f"{str(header_file_path)} exists, not creating.", file=sys.stderr)

        sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(e)
        sys.exit(1)
