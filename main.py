import pandas
import os
from xml.etree import ElementTree as et
from argparse import ArgumentParser
from ast import literal_eval
from dateparser import parse as dateparse
from json import loads as json_decode
from mechanicalsoup import StatefulBrowser
from os.path import join as path_join
from rdflib import Graph
from re import compile as re_compile
from requests import get as requests_get
from zipfile import ZipFile

# hide pandas warnings (all warnings in general)
import warnings
warnings.filterwarnings('ignore')


ALLOWED_EXTS = ('xls', 'xlsx', 'json', 'xml', 'rdf')


def dataframe_from_json(filename):
    try:
        # the file must be opened and parsed into a dict
        with open(filename) as f: json = json_decode(f.read())

        # extract the data attribute
        rows = json['data']
        # extract the columns meta data
        columns = json['meta']['view']['columns']
        # columns names in meta data
        column_names = list(map(lambda c: c['name'], columns))
        # finally, create the dataframe
        dataframe = pandas.DataFrame(rows, columns=column_names)
        return dataframe
    except Exception as e:
        return None


def dataframe_from_xml(filename):

    try:
        # read the xml file
        xtree = et.parse(filename)
        # get the root node
        xroot = xtree.getroot()[0]

        dataframe = pandas.DataFrame()

        # iterate for root node children
        for node in xroot:
            row = []
            for elem in node.getchildren():
                if elem is not None:
                    row.append(elem.text)

            # extract tags from nodes
            df_cols = list(map(lambda c: c.tag, node.getchildren()))
            # create pandas serie with nodes row
            pd_serie = pandas.Series(row, index=df_cols)
            # add row to dataframe
            dataframe = dataframe.append(pd_serie, ignore_index = True)

        return dataframe
    except Exception as e:
        return None


def dataframe_from_rfd(filename):
    try:
        # rdflib reader
        g = Graph()
        # convert into row list
        r = g.parse(filename)
        # finally, create the dataframe
        dataframe = pandas.DataFrame(r)

        return dataframe
    except Exception as e:
        return None


def read_file(filename):
    '''
        Given the input file, generate a dataframe depeding on the file type
    '''

    file_type = filename.rpartition('.')[-1]

    if file_type in ('xlsx', 'xls') :
        dataframe = pandas.read_excel(filename)

    elif file_type == 'csv':
        dataframe = pandas.read_csv(filename)

    elif file_type == 'xml':
        dataframe = dataframe_from_xml(filename)

    elif file_type == 'json':
        dataframe = dataframe_from_json(filename)

    elif file_type == 'rdf':
        dataframe = dataframe_from_rfd(filename)

    else:
        dataframe = None

    return dataframe


def export_csv(dataframe, output_name):
    '''
        Export given dataframe to csv
    '''

    dataframe.to_csv(output_name + '.csv', encoding='utf-8', index=False)


def choose_type_priority(types):
    '''
        The columns may look having multple types
        so we choose the most relevant.
    '''

    if 'str' in types:
        return 'VARCHAR(100)'

    elif 'float' in types:
        return 'DECIMAL(17,4)'

    elif 'int' in types:
        return 'INT'

    elif 'date' in types:
        return 'DATETIME'

    else:
        return 'VARCHAR(100)'


def guess_str_type(value):
    '''
        based in the given value, guess the value type
    '''

    _str = str(value).strip()
    # if 'nan' is received (the numpy None value)
    # there's nothing to do
    if _str == 'nan':
        return None
    elif any([x in _str for x in ('-', '/')]) \
        and len(_str) > 7 \
        and len(_str) < 11 \
        and dateparse(_str) is not None:
        return 'date'

    try:
        # trying to figure out value data type
        return type(literal_eval(_str)).__name__
    except Exception as e:
        # can't cast? we assume it as string
        return 'str'


def identify_colummns_types(dataframe):
    '''
        Iterate each dataframe column to get its types
    '''

    _types = dict()
    # perform the identification process with first 100 rows
    df_partial = dataframe[:100]

    for column in df_partial:
        types = df_partial[column].apply(lambda x: guess_str_type(x)) \
                                  .drop_duplicates() \
                                  .to_list()
        _types[column] = choose_type_priority(types)

    return _types


def filename_and_ext(filename):
    '''
        Split filename in basename and extension
    '''

    base = os.path.basename(filename)
    return base.split('.')[:1] + base.split('.')[-1:]


def generate_sql(dataframe, table_name):
    '''
        Given a dataframe, generate sql script.
    '''

    # columns types
    df_columns = identify_colummns_types(dataframe)
    # get the sql script from dataframe
    sql = pandas.io.sql.get_schema(dataframe,
                                   table_name,
                                   dtype=df_columns)
    return sql


def write_sql(dataframe, filename):
    '''
        Save sql script and write it in filename
    '''

    b_name = os.path.basename(filename)
    sql = generate_sql(dataframe, b_name)

    with open(f'{filename}.sql', 'w') as f:
        f.write(sql)


def single_file(search_tag, filename):
    '''
        Get files with filename included.

        Because in the page could appear same file name multiple times,
        we will get all files with same file name.
    '''

    filename = str(filename)
    root = search_tag.find_all('a', {'title': re_compile(filename)})
    urls = list()

    # iterate over the page content
    for elem in root:
        li = elem.find_parent('li')
        data_format = elem.parent.find('span').attrs['data-format']

        name = li.find('a', {'class' : 'heading'}) \
                     .find(text=True) \
                     .strip() \
                     .replace(' ', '_')

        url = li.find('i', { 'class' : 'icon-download-alt'}) \
                  .parent.attrs['href']

        if name == '':
            name = filename.strip().replace(' ', '_')

        name = f'{name}.{data_format}'
        urls.append((name, url))

    return urls


def many_files(search_tag):
    '''
        Get all file urls in the page body
    '''
    urls = list()
    for elem in search_tag.find_all('i', { 'class' : 'icon-download-alt'}):
        root = elem.find_parent('li')
        name = root.find('a', {'class' : 'heading'}) \
                     .find(text=True) \
                     .strip() \
                     .replace(' ', '_')

        # get file extension
        data_format = elem.parent.attrs['data-format']
        # download url
        url = elem.parent.attrs['href']

        # the file extension is often included
        # if not, then add it
        if f'.{data_format}' not in name:
            name = f'{name}.{data_format}'

        urls.append((name, url))

    return urls


def retreive_download_url(url, filename=None):
    '''
        Retrieve files url from page body.

        If filename is given, filter by it.
    '''

    try:
        br = StatefulBrowser()
        response = br.open(url)
        soup = response.soup
        search_tag = soup.find('ul', {'class' : 'resource-list'})
        title = soup.find('h1', {'itemprop' : 'name'}).text.strip()

        if filename is None:
            urls = many_files(search_tag)
        else:
            urls = single_file(search_tag, filename)
        return title, urls

    except Exception as e:
        raise Exception('Bad URL')


def download_file(destine, filename, url):
    '''
        Download file from url and place it in given path.
    '''

    response = requests_get(url, stream=True)
    f_path = path_join(destine, filename)

    # we expect a different content type
    if 'text/html' in response.headers.get('Content-Type', 'text/html') :
        return False

    # write file by chunks
    f = open(f_path, 'wb')
    for chunk in response.iter_content(chunk_size=1024):
        f.write(chunk)
    f.close()

    return True


def create_folder_structure(root_folder_name):
    '''
        Given the page title, create the folder structure to save the results.
    '''

    if not os.path.exists(root_folder_name):
        os.mkdir(root_folder_name)
        os.mkdir(path_join(root_folder_name, 'download'))
        os.mkdir(path_join(root_folder_name, 'csv'))
        os.mkdir(path_join(root_folder_name, 'sql'))


def arguments():
    '''
        Parse arguments, -h for help
    '''
    parser = ArgumentParser(description='Retrieve files from http://catalog.data.gov'
                                     ' and convert them to csv')
    parser.add_argument('url', type=str, help='site url')
    parser.add_argument('--filename', '-f', nargs='?',
                        help='specify filename pattern to download')

    return parser.parse_args()


def log_unssuported(title, filename):
    print(f'\t # Bad File')


def process_zip(title, filename):
    '''
        Given the zip file, extract valid files and process them.
    '''

    # download folder
    dwn_dest = path_join(title, 'download')

    f_zip = ZipFile(path_join(dwn_dest, filename))
    z_name, z_ext = filename_and_ext(filename)

    # create folder to extract
    if not os.path.exists(path_join(dwn_dest, z_name)):
        os.mkdir(path_join(dwn_dest, z_name))


    # given each file..
    for f_name in f_zip.namelist():
        b_name, b_ext = filename_and_ext(f_name)

        # .. if valid file, then extract it and process it
        if b_ext in ALLOWED_EXTS:
            f_zip.extract(f_name, path_join(dwn_dest, z_name))
            process_file(title, path_join(dwn_dest, z_name, f_name))


def process_file(title, filename):
    '''
        Given the file path and filename, export to csv and sql.
    '''

    b_name, b_ext = filename_and_ext(filename)

    # folders
    csv_dest = path_join(title, 'csv')
    sql_dest = path_join(title, 'sql')
    dwn_dest = path_join(title, 'download')

    df = read_file(path_join(dwn_dest, filename))

    if df is not None:
        print('\tExporting to csv')
        export_csv(df, path_join(csv_dest, b_name))

        print('\tExporting to sql')
        write_sql(df, path_join(sql_dest, b_name))

    else:
        log_unssuported(title, filename)


def sanity_name(filename):
    '''
        Remove invalid characters for file name.
    '''

    to_replace = r'\/:?*|"'
    for chr_ in to_replace:
        filename = filename.replace(chr_, '')
    return filename


def main():
    args = arguments()

    url = args.url
    filename = args.filename
    title, urls = retreive_download_url(url, filename)

    title = sanity_name(title)
    create_folder_structure(title)
    dwn_dest = path_join(title, 'download')

    for file_info in urls:

        f_name = sanity_name(file_info[0])
        b_name, b_ext = filename_and_ext(f_name)
        f_name = '.'.join([b_name, b_ext])
        url = file_info[1]

        print(f'\nProcessing "{f_name}"')
        print('\tDownloading')
        success = download_file(dwn_dest, f_name, url)

        if success:
            if b_ext == 'zip':
                process_zip(title, f_name)
            else:
                process_file(title, f_name)
        else:
            log_unssuported(title, f_name)


if __name__ == '__main__':
    main()
