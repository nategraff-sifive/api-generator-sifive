#!/home/stillson/lpy/bin/python
#!/usr/bin/env python3.7

import json5
import json
import re
import sys
import typing as t
from pathlib import Path
from pprint import pprint as pp
import string

from dataclasses import dataclass

###
# JSON support
###
import json5

JsonType = t.TypeVar('JsonType', dict, list)
CHECK_EXISTANCE = False

@dataclass(frozen=True)
class Register:
    name:str
    offset: int
    width: int

class InvalidOM(Exception):
    pass


def search_json(js_obj: JsonType, regex: re.Pattern) -> t.Generator:
    if isinstance(js_obj, list):
        for i in js_obj:
            for j in search_json(i, regex):
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
            for d in search_json(list(js_obj.values()), regex):
                if d is not None:
                    yield d
    elif isinstance(js_obj, str):
        if regex.match(js_obj):
            yield js_obj


def find_component(om: JsonType, regex_str: str) -> t.List[dict]:
    regex = re.compile(regex_str)

    if isinstance(om, list) or isinstance(om, dict):
        return [i for i in search_json(om, regex) if i is not None]
    else:
        raise InvalidOM


def search_json_field(js_obj: JsonType, field: str,
                      regex: re.Pattern) -> t.Generator:
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
    regex = re.compile(regex_str)

    if isinstance(js_obj, list) or isinstance(js_obj, dict):
        return [i for i in search_json_field(js_obj, field, regex)
                if i is not None]
    else:
        raise InvalidOM


###
# templates
###


def generate_vtable(deviceName: str, regList: t.List[Register]) -> str:
    rv = []

    for a_reg in regList:
        reg_name = a_reg.name.lower()
        size = a_reg.width
        if size not in (8,16,32,64):
            raise Exception('wierd size')
        write_func = f'    void (*v_{deviceName}_{reg_name}_write)' \
                     f'(uint32_t * {deviceName}_base, uint{size}_t data);'
        read_func = f'    uint{size}_t (*v_{deviceName}_{reg_name}_read)' \
                    f'(uint32_t  *{deviceName}_base);'

        rv.append(write_func)
        rv.append(read_func)

    return '\n'.join(rv)

def generate_metal_dev(deviceName: str) -> str:
    #uint32_t *${dev}_base;
    #const struct metal_${dev}_vtable vtable;

    return f'    uint32_t *{deviceName}_base;\n' + \
    f'    const struct metal_{deviceName}_vtable vtable;'

def generate_protos(deviceName: str, regList: t.List[Register]):
    #//void metal_${dev}_write(const struct metal_${dev} *${dev}, uint32_t data,
    #//                     uint32_t enable);
    #//uint32_t metal_${dev}_read(const struct metal_${dev} *${dev});
    #//const struct metal_${dev} *get_metal_${dev}(uint8_t idx);


    rv = []

    dev_struct = f'const struct metal_{deviceName} *{deviceName}'

    for a_reg in regList:
        reg_name = a_reg.name.lower()
        size = a_reg.width
        if size not in (8,16,32,64):
            raise Exception('wierd size')


        write_func = f'void metal_{deviceName}_{reg_name}_write' \
                     f'({dev_struct}, uint{size}_t data);'
        read_func = f'uint{size}_t metal_{deviceName}_{reg_name}_read' \
                    f'({dev_struct});'
        rv.append(write_func)
        rv.append(read_func)

    get_device = f'const struct metal_{deviceName} *get_metal_{deviceName}' \
                 f'(uint8_t index);'
    rv.append((get_device))

    return '\n'.join(rv)

###
# templates
###

METAL_DEV_HDR_TMPL = \
"""#include <metal/compiler.h>
#include <stdint.h>
#include <stdlib.h>

#ifndef ${company}_${dev}${index}_h
#define ${company}_${dev}${index}_h
#define ${cap_dev}_BASE ${base_adress}

struct metal_${dev};

struct metal_${dev}_vtable {
${vtable}
};

struct metal_${dev} {
${metal_dev}
};

__METAL_DECLARE_VTABLE(metal_${dev})

${protos}
#endif
"""

def generate_metal_dev_hdr(company, dev, index, base_address, reglist):
    cap_dev = dev.upper()
    t = string.Template(METAL_DEV_HDR_TMPL)

    arg_dict = {'company': company,
                'dev': dev,
                'cap_dev': dev.upper(),
                'index': str(index),
                'base_adress': hex(base_address),
                'vtable': generate_vtable(dev, reglist),
                'metal_dev': generate_metal_dev(dev),
                'protos': generate_protos(dev, reglist)}

    return t.substitute(**arg_dict)

METAL_DEV_DRV_TMPL = \
"""#include <stdint.h>
#include <stdlib.h>

#include <${dev}/${company}_${dev}${index}.h>
#include <metal/compiler.h>
#include <metal/io.h>

// Note: these macros have control_base as a hidden input
#define METAL_${cap_dev}_REG(offset) (((unsigned long)control_base + offset))
#define METAL_${cap_dev}_REGW(offset) \\
    (__METAL_ACCESS_ONCE((__metal_io_u32 *)METAL_${cap_dev}_REG(offset)))

${offsets}

${base_functions}

${metal_functions}

__METAL_DEFINE_VTABLE(metal_${dev}) = {
${def_vtable}
};

const struct metal_${dev}* ${dev}_tables[] = {&metal_${dev}};
uint8_t ${dev}_tables_cnt = 1;

const struct metal_${dev}* get_metal_${dev}(uint8_t idx)
{
    if (idx >= ${dev}_tables_cnt)
        return NULL;
    return ${dev}_tables[idx];
}

"""

def generate_def_vtable(dev: str, regList: t.List[Register]) -> str:
    rv: t.List[str] = []
    cap_dev:str = dev.upper()
    head = f'    .{dev}_base = (uint32_t *){cap_dev}_BASE,'
    rv.append(head)
    for a_reg in regList:
        reg_name = a_reg.name.lower()

        write_func = f'    .vtable.v_{dev}_{reg_name}_write = {dev}_{reg_name}_write,'
        read_func =  f'    .vtable.v_{dev}_{reg_name}_read = {dev}_{reg_name}_read,'
        rv.append(write_func)
        rv.append(read_func)

    return '\n'.join(rv)

def generate_offsets(deviceName: str, regList: t.List[Register]) -> str:
    rv:t.List[str] = []

    cap_dev = deviceName.upper()
    for a_reg in regList:
        name = a_reg.name.upper()
        offset = a_reg.offset
        macro_line = f'#define METAL_{cap_dev}_{name} {offset}'
        rv.append(macro_line)

    return '\n'.join(rv)

def generate_base_functions(dev: str, regList: t.List[Register]) -> str:
    def clean_str(a_str:str) -> str:
        def post_pipe(b_str: str):
            return b_str.split('|', 1 )[-1]
        return "\n".join(post_pipe(l).rstrip() for l in a_str.splitlines())


    cap_dev = dev.upper()
    rv: t.List[str] = []

    for  a_reg in regList:
        name = a_reg.name.lower()
        cap_name = a_reg.name.upper()
        size = a_reg.width
        if size not in (8,16,32,64):
            raise Exception('wierd size')

        write_func = f"""
            |void {dev}_{name}_write(uint32_t *{dev}_base, uint{size}_t data)
            |{{
            |    volatile uint32_t *control_base = {dev}_base;
            |    METAL_{cap_dev}_REGW(METAL_{cap_dev}_{cap_name}) = data;
            |}}"""



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


def generate_metal_function(dev: str, regList: t.List[Register]) -> str:
    def clean_str(a_str:str) -> str:
        def post_pipe(b_str: str):
            return b_str.split('|', 1 )[-1]
        return "\n".join(post_pipe(l).rstrip() for l in a_str.splitlines())

    rv: t.List[str] = []

    for a_reg in regList:
        name = a_reg.name.lower()
        size = a_reg.width
        if size not in (8,16,32,64):
            raise Exception('wierd size')
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
    cap_dev = dev.upper()
    t = string.Template(METAL_DEV_DRV_TMPL)

    arg_dict = {'company': company,
                'dev': dev,
                'cap_dev': dev.upper(),
                'index': str(index),
                'offsets': generate_offsets(dev, reglist),
                'base_functions': generate_base_functions(dev, reglist),
                'metal_functions':generate_metal_function(dev, reglist),
                'def_vtable':generate_def_vtable(dev, reglist)}

    return t.substitute(**arg_dict)


###
# main
###


def main():
    def hl():
        print('-'* 80)


    print(sys.argv, file=sys.stderr)

    if len(sys.argv) < 5:
        def clean_str(long_str):
            return '\n'.join(i.strip() for i in long_str.splitlines() if i)
        usage = f"""
        {sys.argv[0]} <object model file> <company name> <device name> <metal directory>
        """

        print("Not enough arguments", file=sys.stderr)
        print(f'{clean_str(usage)}', file=sys.stderr)
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
        name:str = a_reg['name']
        offset:int = a_reg['addressOffset'] // 8
        width:int = a_reg['size']

        reglist.append(Register(name, offset, width))

    if False:
        hl()
        print(generate_vtable(device, reglist))
        hl()
        print(generate_metal_dev(device))
        hl()
        print(generate_protos(device, reglist))
        hl()
        print(generate_offsets(device,reglist))
        hl()
        print(generate_base_functions(device, reglist))
        hl()
        print(generate_metal_function(device,reglist))
        hl()

        return

    memory_regions = find_json_field_name(object_model, '_types', 'OMMemoryRegion')
    devices = find_json_field_name(memory_regions, 'name', f'{device}.*')

    devices_enumerated = list(enumerate(devices))

    m_dir_path = Path(metal_dir)
    m_hdr_path = m_dir_path / device

    if not m_hdr_path.exists():
        m_hdr_path.mkdir()

    first = True

    for i, om in devices_enumerated:
        index = i
        base = om['addressSets'][0]['base']

        if first:
            first = False
            driver_file_path = m_dir_path /  f'{company}_{device}.c'
            if not driver_file_path.exists() and not CHECK_EXISTANCE:
                driver_file_path.write_text(generate_metal_dev_drv(company, device, index, reglist))
            else:
                print(f"{str(driver_file_path)} exists, not creating.", file=sys.stderr)

        header_file_path = m_hdr_path / f'{company}_{device}{index}.h'
        if not header_file_path.exists() and not CHECK_EXISTANCE:
            header_file_path.write_text(generate_metal_dev_hdr(company, device, index, base, reglist))
        else:
            print(f"{str(header_file_path)} exists, not creating.", file=sys.stderr)


if __name__ == '__main__':
    try:
        main()
    except:
        sys.exit(1)
    sys.exit(0)
