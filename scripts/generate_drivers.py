#!/usr/bin/env python3.7

import argparse
import json
import re
import string
import sys
import textwrap
import types
import typing as t
from dataclasses import dataclass
from pathlib import Path

import json5

###
# JSON support
###

# type variable to handle our use of json
JSONType = t.TypeVar('JSONType', dict, list, str, int, float, bool, t.Iterator)


@dataclass(frozen=True)
class Register:
    """
    Description of memory-mapped control register within a device
    """
    name: str
    offset: int  # in bytes
    width: int  # in bits


class InvalidParsedJSON(Exception):
    """
    Raised when an object is passed as something that should
    have been parsed from JSON, and isn't a List or a Dictionary.
    """
    pass


def search_json_component(js_obj: JSONType, regex: re.Pattern) -> t.Iterator:
    """
    Search a json object for any key or string matching regex. Return a
    generator for those objects.

    :param js_obj: A parsed javascript object
    :param regex: a compiled regular expression to search for.
    :return: A generator returning matching parts
    """
    if isinstance(js_obj, (list, types.GeneratorType)):
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


def find_component(js_obj: JSONType, regex_str: str) -> t.Iterator:
    """

    :param js_obj: A dict or list from a parsed json file
    :param regex_str: A regular expression string to search js_obj for
    :return: a list of matching parts
    """
    regex = re.compile(regex_str)

    if isinstance(js_obj, (dict, list, types.GeneratorType)):
        return (i for i in search_json_component(js_obj, regex)
                if i is not None)
    else:
        raise InvalidParsedJSON(f"object not dict or list: {js_obj}")


def search_json_field(js_obj: JSONType, field: str,
                      regex: re.Pattern) -> t.Iterator:
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
        elif isinstance(value, (list, types.GeneratorType)):
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
    elif isinstance(js_obj, (list, types.GeneratorType)):
        for i in js_obj:
            for j in search_json_field(i, field, regex):
                if j is not None:
                    yield j


def find_json_field_name(js_obj: JSONType,
                         field: str,
                         regex_str: str) -> t.Iterator:
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

    if isinstance(js_obj, (list, dict, types.GeneratorType)):
        return (i for i in search_json_field(js_obj, field, regex)
                if i is not None)
    else:
        raise InvalidParsedJSON(f"object not dict or list: {js_obj}")


###
# templates
###

def generate_vtable_declarations(device_name: str,
                                 reg_list: t.List[Register]) -> str:
    """
    Generate the vtable entries for a device and set of registers. This
    creates the declarations for function pointers for all the driver functions.
    This is used to provide a single point for all functions that can be used
    for multiple devices.
    :param device_name: the name of the device
    :param reg_list: a list of Register objects for the device
    :return: the c code for the vtable entries
    """

    rv = []

    for a_reg in reg_list:
        reg_name = a_reg.name.lower()
        size = a_reg.width

        write_func = f'    void (*v_{device_name}_{reg_name}_write)(uint32_t * {device_name}_base, uint{size}_t data);'
        read_func = f'    uint{size}_t (*v_{device_name}_{reg_name}_read)(uint32_t  *{device_name}_base);'

        rv.append(write_func)
        rv.append(read_func)

    return '\n'.join(rv)


def generate_metal_vtable_definition(devices_name: str) -> str:
    """
    Generate the vtable and base address variable definitions
    for the given device name

    :param devices_name:
    :return: The c code for the metal device
    """

    return f'    uint32_t *{devices_name}_base;\n' + \
           f'    const struct metal_{devices_name}_vtable vtable;'


def generate_protos(device_name: str, reg_list: t.List[Register]) -> str:
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

        write_func = f'void metal_{device_name}_{reg_name}_write({dev_struct}, uint{size}_t data);'
        read_func = f'uint{size}_t metal_{device_name}_{reg_name}_read({dev_struct});'

        rv.append(write_func)
        rv.append(read_func)

    get_device = f'const struct metal_{device_name} *get_metal_{device_name}' \
                 f'(uint8_t index);'
    rv.append(get_device)

    return '\n'.join(rv)


###
# templates
###

METAL_BASE_HDR_TMPL = \
    """
    #include <metal/compiler.h>
    #include <metal/io.h>

    #ifndef ${vendor}_${device}_h
    #define ${vendor}_${device}_h
    #define ${cap_device}_BASE ${base_address}

    // : these macros have control_base as a hidden input
    #define METAL_${cap_device}_REG(offset) (((unsigned long)control_base + offset))
    #define METAL_${cap_device}_REGW(offset) \\
       (__METAL_ACCESS_ONCE((__metal_io_u32 *)METAL_${cap_device}_REG(offset)))

    ${register_offsets}

    #endif
    """


def generate_offsets(device_name: str, reg_list: t.List[Register]) -> str:
    """
    Generate the register offset macros

    :param device_name: the name of the device
    :param reg_list: the list of registers for the device.
    :return:The offset c macros for the device and registers
    """
    rv: t.List[str] = []

    cap_device = device_name.upper()
    for a_reg in reg_list:
        name = a_reg.name.upper()
        offset = a_reg.offset
        macro_line = f'#define METAL_{cap_device}_{name} {offset}'
        rv.append(macro_line)

    return '\n'.join(rv)


def generate_base_hdr(vendor: str,
                      device: str,
                      base_address: int,
                      reglist: t.List[Register]):
    template = string.Template(textwrap.dedent(METAL_BASE_HDR_TMPL))

    return template.substitute(
        base_address=hex(base_address),
        vendor=vendor,
        device=device,
        cap_device=device.upper(),
        register_offsets=generate_offsets(device, reglist)
    )


# The template for the .h file

METAL_DEV_HDR_TMPL = \
    """
    #include <metal/compiler.h>
    #include <stdint.h>
    #include <stdlib.h>
    #include <${device}/${vendor}_${device}.h>

    #ifndef ${vendor}_${device}${index}_h
    #define ${vendor}_${device}${index}_h

    struct metal_${device};

    struct metal_${device}_vtable {
    ${vtable}
    };

    struct metal_${device} {
    ${metal_device}
    };

    __METAL_DECLARE_VTABLE(metal_${device})

    ${protos}
    #endif
    """


def generate_metal_dev_hdr(vendor, device, index, base_address, reglist):
    """

    :param vendor: The name of the vendor creating the device
    :param device: the name of the device created.
    :param index: the index of the device
    :param base_address: the base memory address of the device
    :param reglist: the list of registers for the device
    :return: a string which is the .h for file the device driver
    """
    template = string.Template(textwrap.dedent(METAL_DEV_HDR_TMPL))

    return template.substitute(
        vendor=vendor,
        device=device,
        cap_device=device.upper(),
        index=str(index),
        base_address=hex(base_address),
        vtable=generate_vtable_declarations(device, reglist),
        metal_device=generate_metal_vtable_definition(device),
        protos=generate_protos(device, reglist)
    )


# the template for the driver .c file
METAL_DEV_DRV_TMPL = \
    """
    #include <stdint.h>
    #include <stdlib.h>

    #include <${device}/${vendor}_${device}${index}.h>
    #include <metal/compiler.h>
    #include <metal/io.h>

    ${base_functions}

    ${metal_functions}

    __METAL_DEFINE_VTABLE(metal_${device}) = {
    ${def_vtable}
    };

    const struct metal_${device}* ${device}_tables[] = {&metal_${device}};
    uint8_t ${device}_tables_cnt = 1;

    const struct metal_${device}* get_metal_${device}(uint8_t idx)
    {
        if (idx >= ${device}_tables_cnt)
            return NULL;
        return ${device}_tables[idx];
    }
    """


def generate_def_vtable(device: str, reg_list: t.List[Register]) -> str:
    """
    Generate vtable settings for vtable declaration in .c file

    :param device: the name of the device
    :param reg_list: the register list for the device
    :return: the declarations in the vtable for the driver .c file
    """
    rv: t.List[str] = []
    cap_device = device.upper()
    head = f'    .{device}_base = (uint32_t *){cap_device}_BASE,'
    rv.append(head)
    for a_reg in reg_list:
        reg_name = a_reg.name.lower()

        write_func = f'    .vtable.v_{device}_{reg_name}_write ' \
                     f'= {device}_{reg_name}_write,'
        read_func = f'    .vtable.v_{device}_{reg_name}_read ' \
                    f'= {device}_{reg_name}_read,'
        rv.append(write_func)
        rv.append(read_func)

    return '\n'.join(rv)


def generate_base_functions(device: str, reg_list: t.List[Register]) -> str:
    """
    Generates the basic, not exported register access functions for
    a given device and register list.

    :param device: the name of the device
    :param reg_list: the list of registers for the device.
    :return:  the c code for the register access functions
    """
    cap_device = device.upper()
    rv: t.List[str] = []

    for a_reg in reg_list:
        name = a_reg.name.lower()
        cap_name = a_reg.name.upper()
        size = a_reg.width

        write_func = f"""
            void {device}_{name}_write(uint32_t *{device}_base, uint{size}_t data)
            {{
                volatile uint32_t *control_base = {device}_base;
                METAL_{cap_device}_REGW(METAL_{cap_device}_{cap_name}) = data;
            }}
            """

        rv.append(textwrap.dedent(write_func))

        read_func = f"""
            uint{size}_t {device}_{name}_read(uint32_t *{device}_base)
            {{
                volatile uint32_t *control_base = {device}_base;
                return METAL_{cap_device}_REGW(METAL_{cap_device}_{cap_name});
            }}
            """

        rv.append(textwrap.dedent(read_func))

    return '\n'.join(rv)


def generate_metal_function(device: str, reg_list: t.List[Register]) -> str:
    """
    Generates the exported register access functions for
    a given device and register list.

    :param device: the name of the device
    :param reg_list: the list of registers for the device.
    :return:  the c code for the exported register access functions
    """

    rv: t.List[str] = []

    for a_reg in reg_list:
        name = a_reg.name.lower()
        size = a_reg.width

        write_func = f"""
            void metal_{device}_{name}_write(const struct metal_{device} *{device}, uint{size}_t data)
            {{
                if ({device} != NULL)
                    {device}->vtable.v_{device}_{name}_write({device}->{device}_base, data);
            }}
            """
        rv.append(textwrap.dedent(write_func))

        read_func = f"""
            uint{size}_t metal_{device}_{name}_read(const struct metal_{device} *{device})
            {{
                if ({device} != NULL)
                    return {device}->vtable.v_{device}_{name}_read({device}->{device}_base);
                return (uint{size}_t)-1;
            }}
            """

        rv.append(textwrap.dedent(read_func))

    return '\n'.join(rv)


def generate_metal_dev_drv(vendor, device, index, reglist):
    """
    Generate the driver source file contents for a given device
    and register list

    :param vendor: the vendor creating the device
    :param device: the device
    :param index: the index of the device used
    :param reglist: the list of registers
    :return: a string containing of the c code for the basic driver
    """
    template = string.Template(textwrap.dedent(METAL_DEV_DRV_TMPL))

    return template.substitute(
        vendor=vendor,
        device=device,
        cap_device=device.upper(),
        index=str(index),
        offsets=generate_offsets(device, reglist),
        base_functions=generate_base_functions(device, reglist),
        metal_functions=generate_metal_function(device, reglist),
        def_vtable=generate_def_vtable(device, reglist)
    )


###
# main
###


def handle_args():
    """
    :return:
    """
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-o",
        "--object-model",
        type=argparse.FileType('r'),
        help="The pat to the object model file",
        required=True,
    )

    parser.add_argument(
        "-d",
        "--duh-document",
        type=argparse.FileType('r'),
        help="The pathe to the DUH document",
        required=True,
    )

    parser.add_argument(
        "--vendor",
        help="The vendor name",
        required=True,
    )

    parser.add_argument(
        "-D",
        "--device",
        help="The device name",
        required=True,
    )

    parser.add_argument(
        "-m",
        "--metal-dir",
        help="The path to the drivers/metal directory",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "-x",
        "--overwrite-existing",
        action="store_true",
        default=False,
        help="overwrite existing files"
    )

    parser.add_argument(
        "-H",
        "--base-header-only",
        action="store_true",
        default=False,
        help="Create only base header file, not drivers"
    )

    return parser.parse_args()


def main():
    """

    :return: exits 0 on success, 1 on failure
    """

    # parse args
    args = handle_args()

    object_model = json.load(args.object_model)
    duh_info = json5.load(args.duh_document)
    vendor = args.vendor
    device = args.device
    m_dir_path = args.metal_dir
    overwrite_existing = args.overwrite_existing
    base_header_only = args.base_header_only

    # process register info from duh
    addr_blocks = [i['addressBlocks'][0] for i in
                   find_component(duh_info, 'addressBlocks')]

    regs = addr_blocks[0]['registers']

    reglist: t.List[Register] = []
    for a_reg in regs:
        name: str = a_reg['name']
        offset: int = a_reg['addressOffset'] // 8
        width: int = a_reg['size']
        if width not in (8, 16, 32, 64):
            raise Exception(f'Invalid register width {width}, for register '
                            f'{name}.\n'
                            f'Width should be not 8, 16, 32, or 64.\n'
                            f'Please fix the register width in DUH document.')

        reglist.append(Register(name, offset, width))

    # parse OM to find base address of all devices
    memory_regions = find_json_field_name(object_model, '_types', 'OMMemoryRegion')
    devices_addr = find_json_field_name(memory_regions, 'name', f'{device}.*')
    devices_addr_enumerated = list(enumerate(devices_addr))

    m_hdr_path = m_dir_path / device

    m_hdr_path.mkdir(exist_ok=True, parents=True)

    for index, om in devices_addr_enumerated:
        base = om['addressSets'][0]['base']

        # use index 0 for operation to only run once
        if index == 0:
            if not base_header_only:
                driver_file_path = m_dir_path / f'{vendor}_{device}.c'
                if driver_file_path.exists() or overwrite_existing:
                    driver_file_path.write_text(
                        generate_metal_dev_drv(vendor, device, index, reglist))
                else:
                    print(f"{str(driver_file_path)} exists, not creating.",
                          file=sys.stderr)

            base_header_path = m_hdr_path / f'{vendor}_{device}.h'
            if not base_header_path.exists() and overwrite_existing:
                base_header_path.write_text(
                    generate_base_hdr(vendor, device, base, reglist)
                )
            else:
                print(f"{str(base_header_path)} exists, not creating.",
                      file=sys.stderr)

        if not base_header_only:
            header_file_path = m_hdr_path / f'{vendor}_{device}{index}.h'
            if not base_header_only and \
                    not header_file_path.exists() or overwrite_existing:
                header_file_path.write_text(
                    generate_metal_dev_hdr(vendor, device, index, base, reglist))
            else:
                print(f"{str(header_file_path)} exists, not creating.",
                      file=sys.stderr)


if __name__ == '__main__':
    main()
